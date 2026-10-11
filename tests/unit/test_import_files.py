"""Importing offers: CSV, JSON, text, paste, clean_url and the inbox folder."""

import csv
import datetime as dt
import io
import json
from pathlib import Path

import pytest

from honestcv import importers
from honestcv.errors import HonestError
from honestcv.importers import import_inbox, import_path, import_text, import_url, read_path
from honestcv.importers.urls import clean_url
from honestcv.state import SeenStore

TODAY = dt.date(2026, 10, 10)
SAMPLE = Path(__file__).resolve().parents[2] / "src" / "honestcv" / "samples" / "en" / "offers" / "inbox" / "sample-offers.json"
COLUMNS = "title company url location countries remote contract level skills skills_nice languages salary_min salary_max currency salary_unit description posted_at valid_through requisition_id".split()


def write_csv(path: Path, rows: list[dict], delimiter: str, encoding: str = "utf-8-sig") -> Path:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=sorted({k for r in rows for k in r}, key=lambda k: COLUMNS.index(k) if k in COLUMNS else 99), delimiter=delimiter)
    writer.writeheader()
    writer.writerows(rows)
    path.write_bytes(buffer.getvalue().encode(encoding))
    return path


def test_import_csv_json(tmp_path):
    offers = import_path(SAMPLE, today=TODAY)
    assert len(offers) == 8 and offers[0].source == "import:json" and offers[0].attribution == "your file sample-offers.json"
    first = offers[0]  # the flat salary fields become a Salary; lists stay lists
    assert (first.title, first.company, first.remote, first.countries) == ("Data Analyst", "Tallgrove Analytics", "remote", ["SE"])
    assert (first.salary.min, first.salary.max, first.salary.currency, first.salary.unit) == (40000, 48000, "SEK", "month")
    assert first.skills_nice == ["pandas", "tableau"] and first.posted_at == dt.date(2026, 10, 6) and first.valid_through == dt.date(2026, 11, 6)
    assert first.id == "085450678e" and offers[2].salary is None and [o.contract for o in offers[:3]] == [[], ["employment"], ["employment"]]
    # JSON shapes: a list, {"_note", "offers"} without any warning, and one object
    one = {"title": "Support Agent", "description": "Answer tickets.", "company": "Mossby Support Services"}
    for body in ([one], {"_note": "Fictional sample.", "offers": [one]}, one):
        path = tmp_path / "shape.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        found, notes = read_path(path, TODAY)
        assert [o.title for o in found] == ["Support Agent"] and notes == []
    bad = tmp_path / "bad.json"
    bad.write_text('{"offers": [\n  {"title": "x",}\n]}', encoding="utf-8")
    with pytest.raises(HonestError) as caught:
        import_path(bad, today=TODAY)
    assert caught.value.line == 2 and "not valid JSON" in caught.value.message and caught.value.hint
    # CSV with commas, with semicolons (Excel in Poland), and in the Windows Central European code page
    row = {"title": "Analityk danych, zespół raportów (k/m)", "company": "Example Bank", "url": "https://praca.example.invalid/oferta/1?utm_source=a&id=3&ref=list#apply",
           "location": "Kraków", "countries": "PL;Poland", "remote": "On-Site", "contract": "B2B; umowa o pracę; mystery", "level": "Senior", "skills": "sql;Power BI",
           "skills_nice": "excel", "languages": "pl;en", "salary_min": "12 000", "salary_max": "15000,50", "currency": "pln", "salary_unit": "mc",
           "description": "Opis stanowiska.", "posted_at": "07.10.2026", "valid_through": "2026-11-07", "requisition_id": "REQ-PL-1", "color": "blue"}  # fmt: skip
    link_only = {"title": "Reporting Analyst", "url": "https://x.invalid/1"}
    for delimiter, encoding in ((",", "utf-8-sig"), (";", "utf-8-sig"), (";", "cp1250")):
        path = write_csv(tmp_path / f"o{ord(delimiter)}{encoding}.csv", [row, link_only, {"title": "No text or link"}, {"description": "No title"}], delimiter, encoding)
        found, notes = read_path(path, TODAY)
        assert len(found) == 2 and found[0].source == "import:csv"
        a, b = found
        assert a.title == "Analityk danych, zespół raportów (k/m)" and a.url == "https://praca.example.invalid/oferta/1?id=3&ref=list"
        assert (a.countries, a.remote, a.contract, a.level, a.skills, a.languages) == (["PL"], "onsite", ["b2b", "employment"], "senior", ["sql", "power bi"], ["pl", "en"])
        assert (a.salary.min, a.salary.max, a.salary.currency, a.salary.unit) == (12000, 15000.5, "PLN", "month")
        assert (a.posted_at, a.valid_through, a.requisition_id, a.thin) == (dt.date(2026, 10, 7), dt.date(2026, 11, 7), "REQ-PL-1", False)
        assert (b.thin, b.description, b.company) == (True, "", None)  # a link and a title only
        assert any("unknown columns: color" in n for n in notes) and sum("skipped" in n for n in notes) == 2
        assert any("mystery" in n for n in notes) and any("row 4" in n for n in notes)
    # a CSV needs no particular column order, and a missing file is a friendly error
    with pytest.raises(HonestError) as caught:
        import_path(tmp_path / "missing.csv", today=TODAY)
    assert "quotes" in caught.value.hint
    pdf = tmp_path / "offer.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(HonestError) as caught:
        import_path(pdf, today=TODAY)
    assert ".pdf" in caught.value.message and "--paste" in caught.value.hint


def test_import_text(tmp_path):
    body = "# Specjalista ds. kadr (k/m)\n\nSzukamy osoby do działu kadr.\n\nWymagania\n- Excel\n- Komunikatywny angielski\n"
    offer = import_text(body)
    assert offer.title == "Specjalista ds. kadr (k/m)" and offer.source == "import:text" and offer.attribution == "your pasted text"
    assert "Wymagania" in offer.description and "- Excel" in offer.description and not offer.thin and len(offer.id) == 10
    named = import_text(body, title="HR Assistant", company="Brennvik Parcel", url="https://x.invalid/job?utm_medium=mail&id=5#top")
    assert (named.title, named.company, named.url) == ("HR Assistant", "Brennvik Parcel", "https://x.invalid/job?id=5")
    assert import_text("Data Analyst\nSQL and Python reports for operations teams, every week.").title == "Data Analyst"
    assert import_text("<h1>Reporting Analyst</h1><p>Weekly reports.</p>").title == "Reporting Analyst"
    assert import_text("Short ad").thin  # a title and nothing else
    assert import_text("Ad", url="javascript:alert(1)").url is None  # only web links are stored
    for empty in ("", "  \n "):
        with pytest.raises(HonestError):
            import_text(empty)
    for name in ("offer.txt", "offer.md"):
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        (found,) = import_path(path, today=TODAY)
        assert found.title == "Specjalista ds. kadr (k/m)" and found.attribution == f"your file {name}" and found.source == "import:text"
    # a link: never fetched, the host is the title unless one is given
    link = import_url("https://jobs.unlisted.invalid/offer/7?utm_source=x")
    assert (link.thin, link.title, link.url, link.description) == (True, "jobs.unlisted.invalid", "https://jobs.unlisted.invalid/offer/7", "")
    assert import_url("https://jobs.unlisted.invalid/offer/7", title="Data Analyst").title == "Data Analyst"
    for bad in ("not a link", "javascript:alert(1)", "ftp://x.invalid/a"):
        with pytest.raises(HonestError):
            import_url(bad)


def test_import_paste(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)

        class Done:
            returncode = 0
            stdout = "Analityk danych\nSzukamy analityka do zespołu raportów, SQL i Excel.\n".encode("utf-8")

        return Done()

    monkeypatch.setattr(importers.subprocess, "run", fake_run)
    monkeypatch.setattr(importers.shutil, "which", lambda name: name if name in ("pbpaste", "powershell", "xclip") else None)
    for platform, expected in (("darwin", ["pbpaste"]), ("win32", ["powershell", "-NoProfile", "-Command"]), ("linux", ["xclip", "-o"])):
        monkeypatch.setattr(importers.sys, "platform", platform)
        calls.clear()
        text = importers.read_clipboard()
        assert calls[0][: len(expected)] == expected
        if platform == "win32":
            assert "Get-Clipboard" in calls[0][-1]
        assert import_text(text).title == "Analityk danych"  # "Imported 1 offer from the clipboard (title from the first line)"
    monkeypatch.setattr(importers.shutil, "which", lambda name: name if name == "wl-paste" else None)
    calls.clear()
    importers.read_clipboard()
    assert calls[0][0] == "wl-paste"
    monkeypatch.setattr(importers.shutil, "which", lambda name: None)
    with pytest.raises(HonestError) as caught:
        importers.read_clipboard()
    assert "clipboard" in caught.value.message and "add-offer FILE.txt" in caught.value.hint


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://x.invalid/job?utm_source=a&utm_medium=b&id=7", "https://x.invalid/job?id=7"),
        ("https://x.invalid/job?fbclid=1&gclid=2&mc_cid=3&mc_eid=4&trk=5&trackingId=6&refId=7", "https://x.invalid/job"),
        ("https://x.invalid/job?UTM_Source=a&TrackingID=b&keep=1", "https://x.invalid/job?keep=1"),
        ("https://x.invalid/job?id=7&ref=list#apply", "https://x.invalid/job?id=7&ref=list"),
        ("https://x.invalid/jobs/1#section", "https://x.invalid/jobs/1"),
        ("https://x.invalid/job?q=a%2Fb&city=Krak%C3%B3w&utm_x=1", "https://x.invalid/job?q=a%2Fb&city=Krak%C3%B3w"),
        ("https://x.invalid/job?utmost=1&refid_x=2&trkx=3", "https://x.invalid/job?utmost=1&refid_x=2&trkx=3"),
        ("  https://x.invalid/job?a=1  ", "https://x.invalid/job?a=1"),
        ("javascript:alert(1)", "javascript:alert(1)"),
        ("mailto:jobs@example.invalid", "mailto:jobs@example.invalid"),
        ("/relative/path?utm_source=a", "/relative/path?utm_source=a"),
    ],
)
def test_clean_url(url, expected):
    assert clean_url(url) == expected


def test_import_inbox(workspace, tmp_path):
    inbox = workspace.inbox
    (inbox / "sample-offers.json").write_bytes(SAMPLE.read_bytes())
    (inbox / "more.csv").write_text("title,url,mood\nReporting Analyst,https://x.invalid/1,happy\n", encoding="utf-8")
    (inbox / "broken.json").write_text("{oops", encoding="utf-8")
    (inbox / "scan.pdf").write_bytes(b"%PDF")
    (inbox / ".DS_Store").write_bytes(b"junk")
    store_path = workspace.state_dir / "state.json"
    store = SeenStore.load(store_path, TODAY)
    offers, status = import_inbox(workspace, store, TODAY)
    assert len(offers) == status.offers == 9 and status.id == "inbox" and status.state == "error" and status.http_status is None
    assert status.message.startswith("4 files, 9 offers") and "unknown columns: mood" in status.message and "broken.json" in status.message
    assert "scan.pdf was ignored" in status.message and ".DS_Store" not in status.message
    before = {p.name: p.read_bytes() for p in inbox.iterdir()}
    store.save()
    # every scan imports every file again, never moves it, and repeats a note only the first time
    second_store = SeenStore.load(store_path, TODAY + dt.timedelta(days=1))
    offers2, status2 = import_inbox(workspace, second_store, TODAY + dt.timedelta(days=1))
    assert [o.id for o in offers2] == [o.id for o in offers] and "mood" not in status2.message and "broken.json" in status2.message
    assert {p.name: p.read_bytes() for p in inbox.iterdir()} == before
    (inbox / "broken.json").unlink()
    (inbox / "scan.pdf").unlink()
    ok = import_inbox(workspace, second_store, TODAY)[1]
    assert (ok.state, ok.message) == ("ok", "2 files, 9 offers")
    for p in list(inbox.iterdir()):
        p.unlink()
    empty_offers, empty = import_inbox(workspace, second_store, TODAY)
    assert (empty_offers, empty.state, empty.offers) == ([], "empty", 0)
