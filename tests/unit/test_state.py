"""The seen store (TTL, atomic save) and dedupe."""

import datetime as dt
import hashlib
import json
import os

import pytest

from honestcv import workspace
from honestcv.importers.urls import finalize_offer
from honestcv.models import Offer, Salary
from honestcv.state import SeenStore, dedupe, dedupe_key

TODAY = dt.date(2026, 10, 10)


def make(title="Data Analyst", company="Tallgrove Analytics", **kw) -> Offer:
    return finalize_offer(Offer(id="", source=kw.pop("source", "import:json"), attribution="your file x.json", title=title, company=company, **kw))


def test_seen_store(tmp_path):
    path = tmp_path / ".honestcv" / "state.json"
    first = SeenStore.load(path, TODAY)  # no file yet
    assert first.is_new("a") and first.is_new("b")
    first.mark("a", "surfaced")
    first.mark("b", "filtered")  # filtered offers are remembered too
    first.mark("c", "below_threshold")
    assert first.is_new("a")  # the answer does not change while a run is in progress
    first.mark_file("f" * 64)
    first.save()
    second = SeenStore.load(path, TODAY + dt.timedelta(days=1))
    assert [second.is_new(k) for k in "abcd"] == [False, False, False, True]
    assert second.file_seen("f" * 64) and not second.file_seen("0" * 64)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["seen"]["b"] == {"date": "2026-10-10", "decision": "filtered"} and saved["schema"] == 1


def test_seen_store_prunes_after_45_days(tmp_path):
    path = tmp_path / "state.json"
    old = {k: {"date": (TODAY - dt.timedelta(days=d)).isoformat(), "decision": "surfaced"} for k, d in (("d44", 44), ("d45", 45), ("d46", 46))}
    path.write_text(json.dumps({"schema": 1, "seen": old, "files": {"hash45": old["d45"]["date"], "hash46": old["d46"]["date"]}}), encoding="utf-8")
    store = SeenStore.load(path, TODAY)
    assert [store.is_new(k) for k in ("d44", "d45", "d46")] == [False, False, True]  # 46 days old has expired
    assert store.file_seen("hash45") and not store.file_seen("hash46")
    store.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(saved["seen"]) == ["d44", "d45"] and sorted(saved["files"]) == ["hash45"]
    assert not SeenStore.load(path, TODAY, ttl_days=10).file_seen("hash45")  # the TTL is a parameter
    store.mark("d46", "surfaced")  # marking again starts a new 45 days
    store.save()
    assert "d46" in json.loads(path.read_text(encoding="utf-8"))["seen"]


def test_seen_store_save_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    store = SeenStore.load(path, TODAY)
    store.mark("a", "surfaced")
    store.save()
    before = path.read_bytes()
    store.mark("b", "surfaced")

    def crash(*args, **kwargs):
        raise OSError("simulated crash before the file is replaced")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        store.save()
    monkeypatch.undo()
    assert path.read_bytes() == before  # the old file is untouched
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]  # and no temporary file is left behind
    assert workspace.atomic_write  # the store writes through workspace.atomic_write


def test_seen_store_survives_a_damaged_file(tmp_path):
    for text in ("{not json", "[]", '{"seen": [1, 2], "files": 3}', '{"seen": {"a": {"date": "yesterday"}}}'):
        path = tmp_path / "state.json"
        path.write_text(text, encoding="utf-8")
        assert SeenStore.load(path, TODAY).is_new("a")


def test_dedupe_key_and_ids():
    assert dedupe_key(make(requisition_id=" Tallgrove-Data-Analyst ")) == "req:tallgrove-data-analyst"
    assert dedupe_key(make(url="https://Jobs.Example.invalid/a/b/?id=7&utm_source=x#top")) == "url:jobs.example.invalid/a/b?id=7"
    text_key = dedupe_key(make(description="Same text."))
    assert text_key.startswith("txt:tallgrove analytics|data analyst|") and dedupe_key(make(description="  same   TEXT. ")) == text_key
    offer = make(requisition_id="tallgrove-data-analyst", url="https://jobs.tallgrove.invalid/openings/data-analyst")
    assert offer.id == hashlib.sha1(b"req:tallgrove-data-analyst").hexdigest()[:10] == "085450678e"  # the id the golden scan sample carries


def test_dedupe(tmp_path):
    a = make(requisition_id="R-1", url="https://jobs.tallgrove.invalid/openings/1", description="Short.", source="himalayas")
    b = make(requisition_id="r-1", url="https://other.invalid/listing/9", description="A much longer description of the same requisition.",
             salary=Salary(min=1, max=2, currency="EUR"), location="Malmo", source="arbeitnow")  # fmt: skip
    merged = dedupe([a, b])
    assert len(merged) == 1
    (one,) = merged
    assert one.description.startswith("A much longer") and one.url == "https://other.invalid/listing/9"
    assert one.other_urls == ["https://jobs.tallgrove.invalid/openings/1"]  # both links are kept
    assert one.salary == Salary(min=1, max=2, currency="EUR") and one.location == "Malmo" and one.id == a.id == b.id
    # same company and title, no requisition id: both stay, the later one is marked
    c = make(url="https://x.invalid/1", description="One.")
    d = make(url="https://y.invalid/2", description="Two.", title="Data  Analyst", company="TALLGROVE analytics")
    e = make(url="https://z.invalid/3", description="Three.", title="Data Analyst", company="Quarry Lane Software")
    out = dedupe([c, d, e])
    assert [o.possible_duplicate_of for o in out] == [None, c.id, None] and len(out) == 3
    # different requisition ids are different openings, even with the same title
    f, g = make(requisition_id="R-7"), make(requisition_id="R-8")
    assert [o.possible_duplicate_of for o in dedupe([f, g])] == [None, None]
    h = make(requisition_id="R-9")  # one side without an id is enough to mark
    assert [o.possible_duplicate_of for o in dedupe([c, h])] == [None, c.id]
    # no company: titles alone never mark offers
    assert [o.possible_duplicate_of for o in dedupe([make(company=None, url="https://p.invalid/1"), make(company=None, url="https://p.invalid/2")])] == [None, None]
    # running it twice gives the same list, in the same order
    again = dedupe(out)
    assert [o.model_dump() for o in again] == [o.model_dump() for o in out]
    assert [o.url for o in dedupe([e, c, d])] == ["https://z.invalid/3", "https://x.invalid/1", "https://y.invalid/2"]  # first-seen order
