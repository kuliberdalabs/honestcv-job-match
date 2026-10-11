"""run_doctor: a read-only health check. These tests use stand-ins for the CV reader and the ledger code."""

import hashlib
import json
import sys

import pytest

import honestcv.sources
from honestcv import doctor
from honestcv.doctor import DoctorCheck, doctor_exit_code, run_doctor
from honestcv.errors import HonestError, LedgerError
from honestcv.models import Claim, CvText, Ledger, LintIssue, SourceStatus
from honestcv.workspace import Workspace, init_workspace

DRAFT_WARN = LintIssue(severity="WARN", path="claims[0]", line=12, message="draft: your own CV line, not yet backed by evidence")


def tree(root):
    """Paths, sizes and hashes of everything under root."""
    return sorted((str(p.relative_to(root)), p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "")
                  for p in root.rglob("*"))  # fmt: skip


def by_name(checks: list[DoctorCheck]) -> dict[str, DoctorCheck]:
    return {c.name: c for c in checks}


@pytest.fixture
def stand_ins(monkeypatch):
    """A CV reader and a ledger loader that read files without caching anything."""

    def read_cv(path):
        if path.suffix == ".doc":
            raise HonestError("Save it as PDF or DOCX, then try again.", path=path.name)
        return CvText(text=path.read_text(encoding="utf-8", errors="replace"), pages=2 if path.suffix == ".pdf" else None)

    state = {"claims": [Claim(id="a", kind="delivery"), Claim(id="b", kind="delivery")], "issues": []}
    monkeypatch.setattr(doctor, "load_cv_text", read_cv)
    monkeypatch.setattr(doctor, "load_ledger", lambda path: Ledger(schema_version=1, person={}, claims=state["claims"]) if state["claims"] else None)
    monkeypatch.setattr(doctor, "lint_ledger", lambda ledger, root, today: state["issues"])
    return state


@pytest.fixture
def ws(sample_workspace, stand_ins) -> Workspace:
    return sample_workspace


def test_doctor_read_only(ws, stand_ins, monkeypatch):
    (ws.out).mkdir(exist_ok=True)
    (ws.out / "latest.json").write_text(json.dumps({"run_id": "2026-10-10T08-12-03", "status": "PASS", "today": "2026-10-10"}), encoding="utf-8")
    before = tree(ws.root)
    run_doctor(ws.root, online=False)
    run_doctor(None, online=False, workspace=ws.root)

    def fake_collect(cfg, tmp_ws, *, offline, today):
        assert tmp_ws.root != ws.root and not offline
        tmp_ws.state_dir.mkdir(parents=True)  # the HTTP cache of a real collect lands in the throwaway workspace
        (tmp_ws.state_dir / "cache.json").write_text("{}", encoding="utf-8")
        return [], [SourceStatus(id="himalayas", state="ok", http_status=200, offers=3, message="3 offers")]

    monkeypatch.setattr(honestcv.sources, "collect", fake_collect)
    assert by_name(run_doctor(ws.root, online=True))["network"].level == "OK"
    assert tree(ws.root) == before
    assert not (ws.state_dir / "lock").exists() and not (ws.state_dir / "profiles").exists()


def test_doctor_on_the_sample_workspace(ws):
    checks = run_doctor(ws.root, online=False)
    named = by_name(checks)
    assert [c.name for c in checks] == ["version", "workspace", "config", "cvs", "ledger", "sources", "network"]
    assert checks[0].level == "INFO" and checks[0].message.startswith("honestcv 0.1.0 · Python 3.")
    assert named["workspace"].level == "OK" and named["workspace"].message == str(ws.root)
    assert named["config"] == DoctorCheck(name="config", level="OK", message="honestcv.yml valid · criteria weights sum to 100")
    assert named["cvs"].level == "OK" and named["cvs"].message == "2 readable (maya-analytics.md, maya-support-ops.md)"
    assert named["ledger"].level == "OK" and "2 claims" in named["ledger"].message
    assert named["sources"] == DoctorCheck(name="sources", level="OK", message="4 enabled · no scan yet")
    assert named["network"] == DoctorCheck(name="network", level="SKIP", message="run honestcv doctor --online to test the sources")
    assert doctor_exit_code(checks) == 0


def test_doctor_findings(tmp_path, ws):
    # broken YAML: an ERROR with its line
    ws.config.write_text("profile:\n  level: mid\n\tyears: 6\n", encoding="utf-8")  # a tab on line 3
    config = by_name(run_doctor(ws.root, online=False))["config"]
    assert config.level == "ERROR" and config.message.startswith("line 3: honestcv.yml could not be read")
    assert "Replace the tab characters with spaces." in config.message
    # weights that do not add up
    ws.config.write_text("scoring:\n  criteria:\n    - {id: a, type: contract, weight: 60}\n    - {id: b, type: language, weight: 55}\n", encoding="utf-8")
    config = by_name(run_doctor(ws.root, online=False))["config"]
    assert config.level == "ERROR" and "115" in config.message and "sum to 100" in config.message
    assert by_name(run_doctor(ws.root, online=False))["sources"].level == "SKIP"
    # exit code: 1 with any ERROR, 0 without
    assert doctor_exit_code(run_doctor(ws.root, online=False)) == 1
    assert doctor_exit_code([DoctorCheck(name="a", level="OK", message=""), DoctorCheck(name="b", level="WARN", message=""),
                             DoctorCheck(name="c", level="SKIP", message=""), DoctorCheck(name="d", level="INFO", message="")]) == 0  # fmt: skip
    assert doctor_exit_code([DoctorCheck(name="a", level="OK", message=""), DoctorCheck(name="b", level="ERROR", message="")]) == 1
    assert doctor_exit_code([]) == 0


def test_outside_a_workspace_the_doctor_says_how_to_start(tmp_path, stand_ins):
    elsewhere = tmp_path / "somewhere"
    elsewhere.mkdir()
    checks = run_doctor(elsewhere, online=False)
    assert [c.name for c in checks] == ["version", "workspace"]
    assert checks[1].level == "ERROR" and "No workspace here" in checks[1].message and "honestcv init" in checks[1].message
    assert doctor_exit_code(checks) == 1
    assert not list(elsewhere.iterdir())


def test_the_workspace_argument_wins_over_the_start_folder(tmp_path, ws):
    elsewhere = tmp_path / "somewhere"
    elsewhere.mkdir()
    assert by_name(run_doctor(elsewhere, online=False, workspace=ws.root))["workspace"].level == "OK"
    assert by_name(run_doctor(ws.root / "cvs", online=False))["workspace"].level == "OK"  # a parent folder is found too
    missing = by_name(run_doctor(elsewhere, online=False, workspace=tmp_path / "nope"))["workspace"]
    assert missing.level == "ERROR" and "does not exist" in missing.message


def test_paths_under_the_home_folder_print_with_a_tilde(_isolated_home, stand_ins):
    root = _isolated_home / "honestcv"
    init_workspace(root)
    assert by_name(run_doctor(None, online=False, workspace=root))["workspace"].message == "~/honestcv"
    assert by_name(run_doctor(None, online=False))["workspace"].message == "~/honestcv"  # found as the default workspace


def test_a_fresh_workspace_needs_a_cv(tmp_path, stand_ins):
    root = tmp_path / "fresh"
    init_workspace(root)
    stand_ins["claims"] = []  # the fresh ledger.yml holds no claims
    checks = run_doctor(root, online=False)
    named = by_name(checks)
    assert named["cvs"].level == "ERROR" and "honestcv add-cv" in named["cvs"].message
    assert named["ledger"].level == "WARN" and "honestcv ledger draft" in named["ledger"].message
    assert named["config"].level == "OK" and doctor_exit_code(checks) == 1


def test_cvs_that_cannot_be_read(ws):
    (ws.cvs / "old.doc").write_bytes(b"x")
    (ws.cvs / ".DS_Store").write_bytes(b"x")
    (ws.cvs / "notes.csv").write_bytes(b"x")  # not a CV format: ignored
    cvs = by_name(run_doctor(ws.root, online=False))["cvs"]
    assert cvs.level == "WARN" and "2 readable" in cvs.message and "old.doc: Save it as PDF or DOCX, then try again." in cvs.message
    assert "DS_Store" not in cvs.message and "notes.csv" not in cvs.message
    for name in ("maya-analytics.md", "maya-support-ops.md"):
        (ws.cvs / name).unlink()
    cvs = by_name(run_doctor(ws.root, online=False))["cvs"]
    assert cvs.level == "ERROR" and cvs.message.startswith("none readable")


def test_a_damaged_cv_does_not_stop_the_check(ws, monkeypatch):
    def damaged(path):
        raise ValueError("EOF marker not found")  # the message of a parser error can quote file content, so it is not shown

    monkeypatch.setattr(doctor, "load_cv_text", damaged)
    cvs = by_name(run_doctor(ws.root, online=False))["cvs"]
    assert cvs.level == "ERROR" and "could not be read (ValueError)" in cvs.message and "EOF marker" not in cvs.message


def test_cv_pages_and_reading_warnings(ws, monkeypatch):
    (ws.cvs / "cv-2026.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(doctor, "load_cv_text", lambda p: CvText(text="x", pages=2 if p.suffix == ".pdf" else None,
                                                                  warnings=["Polish letters in this PDF were read wrongly."] if p.suffix == ".pdf" else []))  # fmt: skip
    cvs = by_name(run_doctor(ws.root, online=False))["cvs"]
    assert cvs.level == "WARN" and "cv-2026.pdf 2 pages" in cvs.message and "cv-2026.pdf: Polish letters in this PDF were read wrongly." in cvs.message


def test_ledger_findings(ws, stand_ins):
    ledger = lambda: by_name(run_doctor(ws.root, online=False))["ledger"]  # noqa: E731
    assert ledger().level == "OK" and ledger().message == "2 claims · lint clean"
    stand_ins["claims"][0].draft = stand_ins["claims"][1].draft = True
    stand_ins["issues"] = [DRAFT_WARN, DRAFT_WARN]
    assert ledger() == DoctorCheck(name="ledger", level="WARN", message="2 draft claims; tailoring and the check accept them only word for word")
    stand_ins["issues"] = [DRAFT_WARN, DRAFT_WARN, LintIssue(severity="WARN", path="claims[1]", line=20, message="review_by 2026-06-01 has passed")]
    assert "1 other warning (run: honestcv ledger lint)" in ledger().message
    stand_ins["issues"] = [LintIssue(severity="ERROR", path="claims[1].confirmed.shipped", line=41, message="state 'shipped' needs evidence of type handover_record")]
    found = ledger()
    assert found.level == "ERROR" and "1 lint error; tailoring is blocked" in found.message and "line 41" in found.message
    stand_ins["claims"] = []
    assert ledger().level == "WARN" and "no claims yet" in ledger().message
    ws.ledger.unlink()
    assert ledger().level == "WARN" and "not found" in ledger().message


def test_an_unreadable_ledger_is_an_error_with_its_line(ws, monkeypatch):
    def broken(path):
        raise LedgerError("ledger.yml could not be read.", hint="Check the indentation.", path="ledger.yml", line=9)

    monkeypatch.setattr(doctor, "load_ledger", broken)
    ledger = by_name(run_doctor(ws.root, online=False))["ledger"]
    assert ledger.level == "ERROR" and ledger.message == "line 9: ledger.yml could not be read. Check the indentation."


def test_sources_and_the_last_scan(ws):
    named = lambda: by_name(run_doctor(ws.root, online=False))["sources"]  # noqa: E731
    assert named().message == "4 enabled · no scan yet"
    ws.out.mkdir(exist_ok=True)
    (ws.out / "latest.json").write_text(json.dumps({"run_id": "2026-10-10T08-12-03", "status": "PASS", "today": "2026-10-10"}), encoding="utf-8")
    assert named() == DoctorCheck(name="sources", level="OK", message="4 enabled · last scan PASS 2026-10-10 08:12")
    (ws.out / "latest.json").write_text(json.dumps({"run_id": "2026-10-10T08-12-03", "status": "PARTIAL"}), encoding="utf-8")
    assert named().level == "WARN" and "last scan PARTIAL 2026-10-10 08:12" in named().message
    (ws.out / "latest.json").write_text("not json", encoding="utf-8")
    assert named().message == "4 enabled · no scan yet"
    ws.config.write_text("search:\n  sources: [himalayas]\n  companies:\n    greenhouse: [examplecorp]\n    lever_eu: [examplesite]\n", encoding="utf-8")
    assert named().message == "3 enabled · no scan yet"  # himalayas, greenhouse and lever


def test_an_unknown_source_name_is_a_warning(ws, monkeypatch):
    monkeypatch.setattr(honestcv.sources, "REGISTRY", {s: {} for s in ("himalayas", "remoteok", "weworkremotely", "arbeitnow")})
    ws.config.write_text("search:\n  sources: [himalayas, himalaya]\n", encoding="utf-8")
    sources = by_name(run_doctor(ws.root, online=False))["sources"]
    assert sources.level == "WARN" and "not a known source: himalaya" in sources.message


def test_online_check_reports_each_source(ws, monkeypatch):
    seen = {}

    def fake_collect(cfg, tmp_ws, *, offline, today):
        seen["keywords"] = cfg.search.keywords
        return [], [SourceStatus(id="himalayas", state="ok", http_status=200, offers=3, message=""),
                    SourceStatus(id="weworkremotely", state="refused", http_status=403, offers=0, message="refused"),
                    SourceStatus(id="inbox", state="ok", http_status=None, offers=0, message="")]  # fmt: skip

    monkeypatch.setattr(honestcv.sources, "collect", fake_collect)
    network = by_name(run_doctor(ws.root, online=True))["network"]
    assert network == DoctorCheck(name="network", level="WARN", message="himalayas ok · weworkremotely refused (HTTP 403)")
    assert seen["keywords"] == ["analyst"]  # a probe search when honestcv.yml names none
    assert doctor_exit_code(run_doctor(ws.root, online=True)) == 0  # a source that is down is a warning, not an error


def test_the_online_check_turns_a_collection_error_into_a_warning(ws, monkeypatch):
    def broken(cfg, tmp_ws, *, offline, today):
        raise HonestError("No network.", hint="Check your connection.")

    monkeypatch.setattr(honestcv.sources, "collect", broken)
    assert by_name(run_doctor(ws.root, online=True))["network"] == DoctorCheck(name="network", level="WARN", message="No network. Check your connection.")


def test_the_header_names_version_python_and_system(ws):
    header = run_doctor(ws.root, online=False)[0]
    assert header.name == "version" and header.level == "INFO"
    assert header.message.startswith(f"honestcv 0.1.0 · Python {sys.version_info.major}.{sys.version_info.minor}.")
