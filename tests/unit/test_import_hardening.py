"""Damaged and hostile input to the importers: deep nesting, huge fields, unreadable links, addresses in links, a link-only paste."""

import csv
import datetime as dt
import json
import time
from email.message import EmailMessage

import pytest

from honestcv import importers
from honestcv.errors import HonestError
from honestcv.importers import import_inbox, import_path, import_text, import_url, read_path
from honestcv.importers.jsonld import job_postings
from honestcv.importers.urls import clean_url, web_link, without_addresses
from honestcv.state import SeenStore

TODAY = dt.date(2026, 10, 10)
POSTING = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Reporting Analyst", "description": "Weekly reports for the finance team.",
           "hiringOrganization": {"@type": "Organization", "name": "Example Bank"}, "url": "https://jobs.example.invalid/reporting-analyst"}  # fmt: skip


def page(*blocks: str, head: str = "") -> str:
    scripts = "".join(f'<script type="application/ld+json">{b}</script>' for b in blocks)
    return f"<!doctype html><html><head>{head}{scripts}</head><body></body></html>"


def alert(path, html: str):
    msg = EmailMessage()
    msg["From"] = "Job Alerts <alerts@example.invalid>"
    msg["To"] = "Maya <maya@example.invalid>"
    msg.set_content("See the HTML version.")
    msg.add_alternative(html, subtype="html")
    path.write_bytes(bytes(msg))
    return path


def nested_graph(levels: int, inner: dict) -> str:
    return '{"@graph":[' * levels + json.dumps(inner) + "]}" * levels


def store_for(workspace) -> SeenStore:
    return SeenStore.load(workspace.state_dir / "state.json", TODAY)


# ---- RECURSION-001: a damaged file costs that file only ----


def test_job_postings_ignore_nodes_nested_deeper_than_50_levels():
    shallow, deep = nested_graph(5, POSTING), nested_graph(100, POSTING)
    assert [p["title"] for p in job_postings([shallow])] == ["Reporting Analyst"]
    assert job_postings([deep]) == []
    assert job_postings([nested_graph(500, POSTING)]) == []  # 1000 containers: the parser itself may refuse it
    assert [p["title"] for p in job_postings(["{broken", nested_graph(500, POSTING), json.dumps(POSTING)])] == ["Reporting Analyst"]


def test_a_500_level_graph_file_next_to_a_good_file(workspace):
    (workspace.inbox / "a-deep.html").write_text(page(nested_graph(500, POSTING)), encoding="utf-8")
    (workspace.inbox / "b-good.html").write_text(page(json.dumps(POSTING)), encoding="utf-8")
    found, notes = read_path(workspace.inbox / "a-deep.html", TODAY)
    assert found == [] and notes == ["a-deep.html: no JobPosting data or readable text found"]
    offers, status = import_inbox(workspace, store_for(workspace), TODAY)
    assert [o.title for o in offers] == ["Reporting Analyst"] and status.offers == 1 and status.state == "ok"
    assert "a-deep.html" in status.message


def test_json_too_deep_for_the_parser_is_a_friendly_error(workspace):
    deep = workspace.inbox / "a-deep.json"
    deep.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    (workspace.inbox / "b-good.json").write_text(json.dumps([{"title": "Support Agent", "description": "Answer tickets."}]), encoding="utf-8")
    with pytest.raises(HonestError) as caught:
        read_path(deep, TODAY)
    assert "a-deep.json" in caught.value.message and "nested too deeply" in caught.value.message and caught.value.hint
    offers, status = import_inbox(workspace, store_for(workspace), TODAY)
    assert [o.title for o in offers] == ["Support Agent"] and status.state == "error"
    assert "a-deep.json" in status.message and "nested too deeply" in status.message


@pytest.mark.parametrize(
    "failure, words",
    [
        (RecursionError(), "nested too deeply"),
        (ValueError("secret text of the file"), "not valid"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "supported encoding"),
        (csv.Error("field larger than field limit (2000000)"), "longer than 2,000,000"),
        (csv.Error("new-line character seen in unquoted field"), "not a readable CSV"),
        (OSError("Permission denied"), "could not be opened"),
    ],
)
def test_one_failing_file_never_stops_the_others(workspace, monkeypatch, failure, words):
    (workspace.inbox / "a-bad.json").write_text("[]", encoding="utf-8")
    (workspace.inbox / "b-good.json").write_text(json.dumps([{"title": "Support Agent", "description": "Answer tickets."}]), encoding="utf-8")
    real = importers.read_path

    def fail_on_bad(path, today):
        if path.name == "a-bad.json":
            raise failure
        return real(path, today)

    monkeypatch.setattr(importers, "read_path", fail_on_bad)  # the failure escapes read_path itself
    offers, status = import_inbox(workspace, store_for(workspace), TODAY)
    assert [o.title for o in offers] == ["Support Agent"] and status.state == "error"
    assert "a-bad.json" in status.message and words in status.message and "secret text" not in status.message
    monkeypatch.setattr(importers, "read_path", real)
    monkeypatch.setattr(importers, "read_offer_file", lambda path: (_ for _ in ()).throw(failure))  # the failure escapes a reader
    with pytest.raises(HonestError) as caught:
        read_path(workspace.inbox / "a-bad.json", TODAY)
    assert "a-bad.json" in caught.value.message and words in caught.value.message and "secret text" not in caught.value.message


# ---- CSV field limit, description cap ----


def write_rows(path, rows: list[dict]):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title", "description"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_a_csv_field_of_150000_characters_imports_and_the_text_is_capped(tmp_path):
    long_text = "Weekly reporting for the finance team. " * 4000  # about 156,000 characters
    assert len(long_text) > 150_000
    (offer,) = import_path(write_rows(tmp_path / "long.csv", [{"title": "Reporting Analyst", "description": long_text}]), today=TODAY)
    assert len(offer.description) == 100_000 and offer.description.startswith("Weekly reporting for the finance team. Weekly")
    assert offer.description == long_text.strip()[:100_000].rstrip()


def test_a_csv_field_over_two_million_characters_is_a_note_for_that_file(workspace):
    write_rows(workspace.inbox / "a-huge.csv", [{"title": "Reporting Analyst", "description": "x" * 2_000_001}])
    write_rows(workspace.inbox / "b-good.csv", [{"title": "Support Agent", "description": "Answer tickets."}])
    with pytest.raises(HonestError) as caught:
        read_path(workspace.inbox / "a-huge.csv", TODAY)
    assert "a-huge.csv" in caught.value.message and "2,000,000" in caught.value.message
    offers, status = import_inbox(workspace, store_for(workspace), TODAY)
    assert [o.title for o in offers] == ["Support Agent"] and status.state == "error"
    assert "a-huge.csv" in status.message and "2,000,000" in status.message


def test_every_importer_keeps_only_the_first_100000_characters(tmp_path):
    text = "Data Analyst\n" + "SQL and Python reports every week. " * 6000
    offer = import_text(text)
    assert len(offer.description) == 100_000 and offer.description.startswith("Data Analyst\nSQL and Python reports")
    assert offer.title == "Data Analyst" and not offer.thin
    path = tmp_path / "offers.json"
    path.write_text(json.dumps([{"title": "Reporting Analyst", "description": "y" * 250_000}]), encoding="utf-8")
    assert [len(o.description) for o in import_path(path, today=TODAY)] == [100_000]
    html = tmp_path / "offer.html"
    html.write_text(page(json.dumps({**POSTING, "description": "z" * 250_000})), encoding="utf-8")
    assert [len(o.description) for o in import_path(html, today=TODAY)] == [100_000]
    short = import_text("Data Analyst\nShort text.")
    assert short.description == "Data Analyst\nShort text."


# ---- links that cannot be read ----


def test_a_link_that_cannot_be_read_is_dropped_not_raised(tmp_path):
    assert web_link("http://[") is None and web_link("https://[::1/x") is None
    assert clean_url("http://[") == "http://[" and without_addresses("http://[a@b") == "http://[a@b"
    with pytest.raises(HonestError):
        import_url("http://[")
    assert import_text("Data Analyst\nSQL reports for the finance team.", url="http://[").url is None
    rows = [{"title": "With text", "url": "http://[", "description": "Weekly reports."}, {"title": "Link only", "url": "http://["},
            {"title": "Fine", "url": "https://x.invalid/1"}]  # fmt: skip
    path = tmp_path / "links.csv"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title", "url", "description"])
        writer.writeheader()
        writer.writerows(rows)
    offers, notes = read_path(path, TODAY)
    assert [(o.title, o.url) for o in offers] == [("With text", None), ("Fine", "https://x.invalid/1")]
    assert sum("the url is not a web link and was left out" in n for n in notes) == 2
    assert any("row 3" in n and "skipped" in n for n in notes)
    as_json = tmp_path / "links.json"
    as_json.write_text(json.dumps(rows), encoding="utf-8")
    offers, notes = read_path(as_json, TODAY)
    assert [(o.title, o.url) for o in offers] == [("With text", None), ("Fine", "https://x.invalid/1")] and any("item 2" in n and "skipped" in n for n in notes)
    saved = tmp_path / "saved.html"
    saved.write_text(page(json.dumps({**POSTING, "url": "http://["})), encoding="utf-8")
    (kept,) = import_path(saved, today=TODAY)
    assert kept.title == "Reporting Analyst" and kept.url is None
    plain_page = tmp_path / "plain.html"
    plain_page.write_text('<html><head><link rel="canonical" href="http://["></head><body><h1>Reporting Analyst</h1><p>Weekly reports for finance.</p></body></html>', encoding="utf-8")
    (fallback,) = import_path(plain_page, today=TODAY)
    assert fallback.title == "Reporting Analyst" and fallback.url is None
    mail = alert(tmp_path / "bad.eml", '<a href="http://[">Broken Link Analyst</a><a href="https://jobs.example.invalid/openings/data-analyst">Data Analyst</a>')
    assert [o.title for o in import_path(mail, today=TODAY)] == ["Data Analyst"]


# ---- REDOS-001: many unclosed script tags ----


def test_many_unclosed_script_tags_are_read_in_linear_time(tmp_path):
    flood = "<script type=application/ld+json> " * 20_000
    mail = alert(tmp_path / "flood.eml", f'<html><body>{flood}<a href="https://jobs.example.invalid/openings/data-analyst">Data Analyst</a></body></html>')
    started = time.perf_counter()
    offers, notes = read_path(mail, TODAY)
    assert time.perf_counter() - started < 5  # generous for slow CI runners; the quadratic version takes minutes
    assert offers == [] or [o.title for o in offers] == ["Data Analyst"]  # an unclosed script swallows the rest of the page
    saved = tmp_path / "flood.html"
    saved.write_text(f"<html><body>{flood}</body></html>", encoding="utf-8")
    started = time.perf_counter()
    assert import_path(saved, today=TODAY) == []
    assert time.perf_counter() - started < 5  # generous for slow CI runners; the quadratic version takes minutes
    # a closed block is found whatever the order of the attributes
    tidy = alert(tmp_path / "tidy.eml", f'<script data-x="1" id="a" type="application/ld+json; charset=utf-8">{json.dumps(POSTING)}</script>')
    assert [o.title for o in import_path(tidy, today=TODAY)] == ["Reporting Analyst"]


# ---- EML-LEAK-001: no e-mail address in a stored link ----


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://jobs.example.invalid/alert/alert.recipient%40example.invalid/view/da-123", "https://jobs.example.invalid/alert/view/da-123"),
        ("https://jobs.example.invalid/alert/alert.recipient%40EXAMPLE.invalid/view", "https://jobs.example.invalid/alert/view"),
        ("https://jobs.example.invalid/alert/alert.recipient@example.invalid/view", "https://jobs.example.invalid/alert/view"),
        ("https://jobs.example.invalid/alert/alert.recipient%2540example.invalid/view", "https://jobs.example.invalid/alert/view"),
        ("https://jobs.example.invalid/alert/alert.recipient%40example.invalid", "https://jobs.example.invalid/alert"),
        ("https://jobs.example.invalid/view?recipient=maya%40example.invalid&id=7", "https://jobs.example.invalid/view?id=7"),
        ("https://jobs.example.invalid/view?id=7&to=maya@example.invalid", "https://jobs.example.invalid/view?id=7"),
        ("https://jobs.example.invalid/view?recipient=maya%40example.invalid", "https://jobs.example.invalid/view"),
        ("https://jobs.example.invalid/view?next=https%3A%2F%2Fa.invalid%2Fx%3Fto%3Dmaya%2540example.invalid&id=7", "https://jobs.example.invalid/view?id=7"),
        ("https://maya%40example.invalid:pw@example.invalid/openings/1", "https://example.invalid/openings/1"),
        ("https://jobs.example.invalid/@company/openings?handle=@company&id=7", "https://jobs.example.invalid/@company/openings?handle=@company&id=7"),
        ("https://jobs.example.invalid/openings/7?id=7&ref=list", "https://jobs.example.invalid/openings/7?id=7&ref=list"),
    ],
)
def test_addresses_are_removed_from_links(url, expected):
    assert without_addresses(url) == expected
    assert web_link(url) == expected
    assert web_link(expected) == expected  # nothing more to remove the second time


def test_addresses_are_removed_from_links_in_mails_and_saved_pages(tmp_path):
    leaky_path = "https://jobs.example.invalid/alert/alert.recipient%40example.invalid/view/da-123?utm_source=mail"
    posting = {**POSTING, "url": "https://jobs.example.invalid/offers/7?recipient=maya%40example.invalid&id=7"}
    html = (f'<a href="{leaky_path}">Data Analyst</a><a href="https://jobs.example.invalid/c/maya%40example.invalid/9">Support Analyst</a>'
            f'<script type="application/ld+json">{json.dumps(posting)}</script>')  # fmt: skip
    mail = alert(tmp_path / "alert.eml", html)
    offers = import_path(mail, today=TODAY)
    assert {o.title: o.url for o in offers} == {"Data Analyst": "https://jobs.example.invalid/alert/view/da-123", "Support Analyst": "https://jobs.example.invalid/c/9",
                                                "Reporting Analyst": "https://jobs.example.invalid/offers/7?id=7"}  # fmt: skip
    plain_mail = tmp_path / "plain.eml"
    msg = EmailMessage()
    msg.set_content("Your alerts\nData Analyst\nhttps://jobs.example.invalid/alert/alert.recipient%40example.invalid/view/da-123\n")
    plain_mail.write_bytes(bytes(msg))
    assert [o.url for o in import_path(plain_mail, today=TODAY)] == ["https://jobs.example.invalid/alert/view/da-123"]
    saved = tmp_path / "saved.html"
    saved.write_text(page(json.dumps(posting), head='<link rel="canonical" href="https://jobs.example.invalid/c/maya%40example.invalid/offer">'), encoding="utf-8")
    (from_page,) = import_path(saved, today=TODAY)
    assert from_page.url == "https://jobs.example.invalid/offers/7?id=7"
    no_posting = tmp_path / "no-posting.html"
    no_posting.write_text('<html><head><link rel="canonical" href="https://jobs.example.invalid/c/maya%40example.invalid/offer"></head><body><h1>Analyst</h1><p>Weekly reports.</p></body></html>', encoding="utf-8")
    (from_text,) = import_path(no_posting, today=TODAY)
    assert from_text.url == "https://jobs.example.invalid/c/offer"
    dump = json.dumps([o.model_dump(mode="json") for o in [*offers, from_page, from_text]])
    assert "maya" not in dump and "alert.recipient" not in dump and "%40" not in dump


# ---- F12: a paste that is only a link ----

LINK_ONLY = "That is only a link. honestcv does not open links. Open the offer in your browser, copy its text, then run: honestcv add-offer --paste"


@pytest.mark.parametrize(
    "text",
    ["https://www.pracuj.pl/praca/analityk-danych-krakow,oferta,1003456789", "  https://jobs.example.invalid/offer/7?utm_source=a&id=3\n\n", "<https://jobs.example.invalid/offer/7>",
     "HTTPS://JOBS.EXAMPLE.INVALID/OFFER/7", "http://jobs.example.invalid/a&amp;b"],
)
def test_pasting_only_a_link_is_refused_with_the_next_step(text):
    with pytest.raises(HonestError) as caught:
        import_text(text)
    assert caught.value.message == LINK_ONLY and "add-offer --url" in caught.value.hint
    with pytest.raises(HonestError):
        import_text(text, title="Data Analyst")


def test_text_with_a_link_in_it_is_still_an_offer(tmp_path):
    offer = import_text("Data Analyst\nApply at https://jobs.example.invalid/offer/7 before Friday.")
    assert offer.title == "Data Analyst" and "https://jobs.example.invalid/offer/7" in offer.description
    assert import_text("See https://jobs.example.invalid/offer/7").title == "See https://jobs.example.invalid/offer/7"
    path = tmp_path / "link.txt"  # a file in the inbox is not newly refused
    path.write_text("https://jobs.example.invalid/offer/7\n", encoding="utf-8")
    assert len(import_path(path, today=TODAY)) == 1


def test_an_offers_file_deeper_than_50_levels_is_refused_on_every_python(tmp_path):
    """Python 3.14 parses nesting that 3.11 refuses; the depth limit gives the same answer on both."""
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 60 + "]" * 60, encoding="utf-8")
    with pytest.raises(HonestError) as caught:
        read_path(deep, TODAY)
    assert caught.value.message == "deep.json could not be read (it is nested too deeply)."
    fine = tmp_path / "fine.json"
    fine.write_text(json.dumps({"offers": [{"title": "Support Agent", "description": "Answer tickets.", "skills": ["sql"]}]}), encoding="utf-8")
    assert [o.title for o in read_path(fine, TODAY)[0]] == ["Support Agent"]
