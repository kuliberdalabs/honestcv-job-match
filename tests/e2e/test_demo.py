"""The demo end to end in a child process, its frozen snapshot, and determinism across hash seeds."""

import json

import pytest

from honestcv.models import ScanRun

RUN = "out/runs/2026-10-10T00-00-00/scan.json"


def test_demo_e2e(demo_run, schemas, validate):
    result, out = demo_run
    stdout = result.stdout.decode("utf-8")
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "Traceback" not in stdout + result.stderr.decode("utf-8", "replace")
    head = stdout.splitlines()[0]
    assert head.startswith("honestcv 0.1.0 · demo · offline") and "sample date 2026-10-10" in head
    # Row 1 is the worked example in docs/scoring.md; the planted letter has its frozen summary.
    assert "  1   83%   CORE       Data Analyst · Tallgrove Analytics" in stdout
    assert "BLOCK · 4 lines blocked · 0 lines to review · 1 line passed" in stdout
    assert "Read 8 sample offers; scored 4 against 2 CV variants." in stdout
    assert "Filtered out: 4 (" in stdout
    assert "&&" not in stdout
    for name in ("report-latest.html", "report-latest.md", "report-2026-10-10.html", "report-2026-10-10.md", "matches.csv", "latest.json"):
        assert (out / "out" / name).is_file(), name
    run = ScanRun.model_validate_json((out / RUN).read_text(encoding="utf-8"))
    assert run.status == "PASS" and run.today.isoformat() == "2026-10-10"
    assert sorted(run.gate_counts) == ["commission_only", "deny_title", "expired", "ledger_ceiling"]
    latest = json.loads((out / "out" / "latest.json").read_text(encoding="utf-8"))
    assert validate(latest, schemas["honestcv.latest.v1"]) == []
    assert len(latest["new_matches"]) == 4


def test_demo_snapshot(demo_run, golden_dir):
    snapshot = golden_dir / "demo_snapshot.json"
    if not snapshot.is_file():
        pytest.skip("tests/golden/demo_snapshot.json is missing (save the demo output there to create it)")
    _, out = demo_run
    assert json.loads((out / RUN).read_text(encoding="utf-8")) == json.loads(snapshot.read_text(encoding="utf-8"))


def test_determinism(tmp_path, run_child):
    """The demo under two hash seeds gives byte-identical scan.json, latest.json and Markdown reports."""
    outputs = []
    for seed in ("1", "2"):
        base = tmp_path / f"seed{seed}"
        home = base / "home"
        home.mkdir(parents=True)
        result = run_child(["demo", "--no-open", "--out", str(base / "out")], cwd=base, home=home, extra_env={"PYTHONHASHSEED": seed})
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        outputs.append([(base / "out" / name).read_bytes() for name in (RUN, "out/latest.json", "out/report-latest.md", "out/matches.csv")])
    assert outputs[0] == outputs[1]


def test_demo_refuses_a_folder_with_files(cli, tmp_path):
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "notes.txt").write_text("keep me", encoding="utf-8")
    result = cli("demo", "--no-open", "--out", taken)
    assert result.exit_code == 1
    assert "not an empty folder" in result.stderr and "Fix: " in result.stderr
    assert sorted(p.name for p in taken.iterdir()) == ["notes.txt"]
