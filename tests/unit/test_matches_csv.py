"""out/matches.csv: one row per surfaced offer per run, appended."""

import csv
import json

import pytest

from honestcv.errors import HonestError
from honestcv.models import ScanRun
from honestcv.report import matches
from honestcv.report.matches import COLUMNS, append_matches_csv

RANKED = [("085450678e", 83), ("53650315c7", 68), ("9735a2608f", 64), ("ab19b2740d", 62), ("0b31598cd4", 61), ("9cdf65a1a9", 59)]


@pytest.fixture
def run(golden_dir) -> ScanRun:
    return ScanRun.model_validate(json.loads((golden_dir / "scan_run_sample.json").read_text(encoding="utf-8")))


def read(path, delimiter):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle, delimiter=delimiter))


def test_matches_csv(run, tmp_path):
    path = tmp_path / "out" / "matches.csv"
    assert append_matches_csv(run, path, delimiter=",") == 6
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and not raw.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
    assert append_matches_csv(run, path, delimiter=",") == 6
    rows = read(path, ",")
    assert rows[0] == COLUMNS == ["scan_date", "run_id", "match", "layer", "title", "company", "best_cv", "source", "url", "new"]
    assert sum(r == rows[0] for r in rows) == 1  # one header, written at creation
    assert len(rows) == 1 + 2 * 6
    first, second = rows[1:7], rows[7:]
    assert first == second  # same run, same rows, in rank order
    assert [int(r[2]) for r in first] == [m for _, m in RANKED]
    top = first[0]
    assert top[:4] == ["2026-10-10", "2026-10-10T00-00-00", "83", "CORE"]
    assert top[4:] == ["Data Analyst", "Tallgrove Analytics", "maya-analytics.md", "import:json", "https://jobs.tallgrove.invalid/openings/data-analyst", "yes"]
    assert [r[9] for r in first] == ["yes", "no", "no", "no", "yes", "yes"]
    titles = [r[4] for r in first]
    # gated, low-confidence and below-threshold offers never get a row
    assert not {"Sales Representative", "Revenue Reporting Analyst", "Branch Office Coordinator"} & set(titles)
    assert "Data Analyst" in titles and sum(t == "Data Analyst" for t in titles) == 1  # not the title-only one


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_delimiters_round_trip_with_polish_letters_and_commas(run, tmp_path, delimiter):
    polish = next(o for o in run.offers if o.id == "9cdf65a1a9")
    polish.title = "Analityk danych, zespół raportów; Łódź (k/m)"
    path = tmp_path / "matches.csv"
    append_matches_csv(run, path, delimiter=delimiter)
    rows = read(path, delimiter)
    assert all(len(r) == len(COLUMNS) for r in rows)
    assert rows[6][4] == "Analityk danych, zespół raportów; Łódź (k/m)"
    assert path.read_text(encoding="utf-8-sig").splitlines()[0].startswith(f"scan_date{delimiter}run_id{delimiter}match")


def test_a_changed_delimiter_rewrites_the_old_rows(run, tmp_path):
    path = tmp_path / "matches.csv"
    append_matches_csv(run, path, delimiter=",")
    append_matches_csv(run, path, delimiter=";")
    rows = read(path, ";")
    assert len(rows) == 1 + 12 and all(len(r) == len(COLUMNS) for r in rows)
    assert rows[0] == COLUMNS and rows[1][4] == "Data Analyst"


def test_cells_that_a_spreadsheet_would_run_as_formulas_are_neutralised(run, tmp_path):
    offer = next(o for o in run.offers if o.id == "085450678e")
    offer.title, offer.company = '=HYPERLINK("https://tracker.invalid/p","x")', "@SUM(A1)"
    path = tmp_path / "matches.csv"
    append_matches_csv(run, path, delimiter=",")
    row = read(path, ",")[1]
    assert row[4].startswith("'=") and row[5].startswith("'@")


def test_text_with_line_breaks_stays_on_one_row(run, tmp_path):
    next(o for o in run.offers if o.id == "085450678e").title = "Data\nAnalyst\r\n(remote)"
    path = tmp_path / "matches.csv"
    append_matches_csv(run, path, delimiter=",")
    assert read(path, ",")[1][4] == "Data Analyst (remote)"
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 7


def test_an_existing_file_without_a_final_newline_still_gets_clean_rows(run, tmp_path):
    path = tmp_path / "matches.csv"
    append_matches_csv(run, path, delimiter=",")
    path.write_bytes(path.read_bytes().rstrip(b"\r\n"))
    append_matches_csv(run, path, delimiter=",")
    assert len(read(path, ",")) == 13


def test_nothing_surfaced_writes_a_header_and_no_rows(run, tmp_path):
    for r in run.results:
        r.surfaced = False
    path = tmp_path / "matches.csv"
    assert append_matches_csv(run, path, delimiter=",") == 0
    assert read(path, ",") == [COLUMNS]
    assert append_matches_csv(run, path, delimiter=",") == 0 and read(path, ",") == [COLUMNS]


def test_the_write_leaves_no_temporary_file(run, tmp_path):
    path = tmp_path / "matches.csv"
    append_matches_csv(run, path, delimiter=",")
    assert [p.name for p in tmp_path.iterdir()] == ["matches.csv"]


def result(run, offer_id, cv_id):
    return next(r for r in run.results if r.offer_id == offer_id and r.cv_id == cv_id)


def titles(run, tmp_path):
    path = tmp_path / "rank.csv"
    path.unlink(missing_ok=True)
    append_matches_csv(run, path, delimiter=",")
    return [r[4] for r in read(path, ",")[1:]]


def test_ties_break_by_newer_posting_then_offer_id(run, tmp_path):
    tallgrove, quarry = (next(o for o in run.offers if o.id == i) for i in ("085450678e", "53650315c7"))
    result(run, quarry.id, "maya-analytics.md").match_pct = 83
    assert titles(run, tmp_path)[:2] == ["Data Analyst", "Analytics Engineer"]  # Tallgrove was posted on 2026-10-06, Quarry Lane on 10-03
    quarry.posted_at = tallgrove.posted_at.replace(day=9)
    assert titles(run, tmp_path)[:2] == ["Analytics Engineer", "Data Analyst"]
    quarry.posted_at = tallgrove.posted_at
    assert titles(run, tmp_path)[:2] == ["Data Analyst", "Analytics Engineer"]  # same day: offer id 085450678e sorts before 53650315c7
    quarry.posted_at = None
    assert titles(run, tmp_path)[:2] == ["Data Analyst", "Analytics Engineer"]  # no date sorts last


def test_the_best_cv_is_the_best_surfaced_result(run, tmp_path):
    # the higher score is under the threshold of its layer, so the other CV is the one that surfaces the offer
    result(run, "53650315c7", "maya-analytics.md").surfaced = False
    result(run, "53650315c7", "maya-support-ops.md").surfaced = True
    path = tmp_path / "best.csv"
    append_matches_csv(run, path, delimiter=",")
    row = next(r for r in read(path, ",") if r[4] == "Analytics Engineer")
    assert (row[2], row[6]) == ("42", "maya-support-ops.md")


def test_a_file_in_another_encoding_is_reported_not_overwritten(run, tmp_path):
    path = tmp_path / "matches.csv"
    saved_by_a_spreadsheet = "scan_date;title\r\n2026-10-09;Specjalista ds. kadr zespół\r\n".encode("cp1250")
    path.write_bytes(saved_by_a_spreadsheet)
    with pytest.raises(HonestError) as caught:
        append_matches_csv(run, path, delimiter=";")
    assert "not UTF-8" in caught.value.message and caught.value.hint
    assert path.read_bytes() == saved_by_a_spreadsheet


def test_a_file_held_open_by_a_spreadsheet_is_reported(run, tmp_path, monkeypatch):
    def locked(path, data):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(matches, "atomic_write", locked)
    with pytest.raises(HonestError) as caught:
        append_matches_csv(run, tmp_path / "matches.csv", delimiter=",")
    assert "open in another program" in caught.value.message and "Close it" in caught.value.hint
