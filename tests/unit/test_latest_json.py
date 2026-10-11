"""out/latest.json: the pointer an agent reads after a scheduled run (schema honestcv.latest.v1)."""

import json
from pathlib import PurePosixPath

import pytest

from honestcv.models import ScanRun
from honestcv.report.matches import latest_json
from honestcv.workspace import Workspace

KEYS = ["schema", "run_id", "today", "status", "report_html", "report_md", "scan_json", "matches_csv", "new_matches"]
MATCH_KEYS = ["match", "layer", "title", "company", "best_cv", "url"]


@pytest.fixture
def run(golden_dir) -> ScanRun:
    return ScanRun.model_validate(json.loads((golden_dir / "scan_run_sample.json").read_text(encoding="utf-8")))


@pytest.fixture
def ws(tmp_path) -> Workspace:
    return Workspace.for_root(tmp_path / "ws")


def validate(data: dict) -> None:
    """The latest.json shape, written out here so this test does not depend on docs/agents.md."""
    assert list(data) == KEYS
    assert data["schema"] == "honestcv.latest.v1"
    assert all(isinstance(data[k], str) for k in KEYS[:-1])
    assert data["status"] in ("PASS", "PARTIAL", "FAIL")
    for key in ("report_html", "report_md", "scan_json", "matches_csv"):
        path = PurePosixPath(data[key])
        assert not path.is_absolute() and ".." not in path.parts and "\\" not in data[key] and ":" not in data[key]
    assert isinstance(data["new_matches"], list)
    for match in data["new_matches"]:
        assert list(match) == MATCH_KEYS
        assert isinstance(match["match"], int) and match["layer"] in ("CORE", "STRETCH", "DOWNSHIFT")
        assert isinstance(match["title"], str) and isinstance(match["best_cv"], str)
        assert match["company"] is None or isinstance(match["company"], str)
        assert match["url"] is None or isinstance(match["url"], str)


def test_latest_json(run, ws):
    text = latest_json(run, ws)
    assert text.endswith("\n")
    data = json.loads(text)
    validate(data)
    assert data["run_id"] == "2026-10-10T00-00-00" and data["today"] == "2026-10-10" and data["status"] == "PARTIAL"
    assert data["report_html"] == "out/report-2026-10-10.html"
    assert data["report_md"] == "out/report-2026-10-10.md"
    assert data["scan_json"] == "out/runs/2026-10-10T00-00-00/scan.json"
    assert data["matches_csv"] == "out/matches.csv"
    assert str(ws.root) not in text  # workspace-relative, never absolute


def test_new_matches_are_the_surfaced_new_offers_only(run, ws):
    assert "9471e29063" in run.new_offer_ids  # new, but a title-only offer: not surfaced
    matches = json.loads(latest_json(run, ws))["new_matches"]
    assert [(m["match"], m["title"]) for m in matches] == [
        (83, "Data Analyst"),
        (61, "<img src=https://tracker.invalid/p>"),
        (59, "Analityk danych, zespół raportów (k/m)"),
    ]
    assert matches[0] == {"match": 83, "layer": "CORE", "title": "Data Analyst", "company": "Tallgrove Analytics",
                          "best_cv": "maya-analytics.md", "url": "https://jobs.tallgrove.invalid/openings/data-analyst"}  # fmt: skip
    assert matches[2]["best_cv"] == "maya-support-ops.md"


def test_no_new_offers_gives_an_empty_list(run, ws):
    run.new_offer_ids = []
    data = json.loads(latest_json(run, ws))
    validate(data)
    assert data["new_matches"] == []


def test_polish_letters_are_written_as_they_are(run, ws):
    assert "zespół raportów" in latest_json(run, ws)


def test_missing_company_and_url_are_null(run, ws):
    offer = next(o for o in run.offers if o.id == "085450678e")
    offer.company, offer.url = None, None
    data = json.loads(latest_json(run, ws))
    validate(data)
    assert data["new_matches"][0]["company"] is None and data["new_matches"][0]["url"] is None


def test_the_run_id_of_a_second_run_on_the_same_day_is_kept(run, ws):
    run.run_id = "2026-10-10T00-00-00-2"
    assert json.loads(latest_json(run, ws))["scan_json"] == "out/runs/2026-10-10T00-00-00-2/scan.json"
