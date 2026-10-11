"""The CLI surface: friendly errors, Windows safety, the default workspace, init, and the
screens printed after each command."""

import io
import json
import sys

import pytest

from honestcv import cli as cli_module
from honestcv import pipeline
from honestcv import profile as prof
from honestcv.config import default_config_text
from honestcv.sources.http import UNLISTED_HOST_MESSAGE
from honestcv.textutil import safe_name
from honestcv.workspace import Workspace, init_workspace

TODAY = ["--today", "2026-10-10"]


def assert_friendly(result, code: int) -> None:
    """No traceback, a Fix: line on stderr, no pydantic input value, and the documented exit code."""
    assert result.exit_code == code, result.output
    assert "Traceback" not in result.output
    assert any(line.startswith("Fix: ") for line in result.stderr.splitlines()), result.stderr
    assert "input_value" not in result.output


def tree(root) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ---- test_cli_friendly_errors ----


def test_cli_friendly_errors_invalid_config(cli, sample_workspace):
    config = sample_workspace.config
    config.write_text(config.read_text(encoding="utf-8").replace("remote: [remote, hybrid]", 'remote: "PRIVATE CV LINE 4711"'), encoding="utf-8")
    result = cli("scan", "--offline", "--no-open", "--workspace", sample_workspace.root, *TODAY)
    assert_friendly(result, 2)
    assert "Where: honestcv.yml line 11" in result.stderr
    assert "PRIVATE CV LINE" not in result.output


def test_cli_friendly_errors_ledger_value_never_echoed(cli, sample_workspace):
    sample_workspace.ledger.write_text("schema_version: 1\nperson: {}\nclaims:\n  - id: x\n    kind: PRIVATE-LEDGER-WORDS\n", encoding="utf-8")
    result = cli("check", sample_workspace.root / "letters" / "cover-letter-planted.md", "--workspace", sample_workspace.root, *TODAY)
    assert_friendly(result, 2)
    assert "Where: ledger.yml line" in result.stderr
    assert "PRIVATE-LEDGER-WORDS" not in result.output


def test_cli_friendly_errors_missing_workspace(cli):
    result = cli("ledger", "lint")
    assert_friendly(result, 1)
    assert "No workspace here." in result.stderr and "honestcv init" in result.stderr


def test_cli_friendly_errors_unlisted_url(cli, sample_workspace, monkeypatch):
    """A fetch outside the registry allow-list stops before any socket use, with advice to import the offer instead."""
    from honestcv.sources import USER_AGENT, allowed_hosts
    from honestcv.sources.http import HttpClient

    def fetch_unlisted(cfg, ws, *, offline, today):
        client = HttpClient(ws.state_dir / "cache", offline=False, user_agent=USER_AGENT, allowed_hosts=allowed_hosts())
        client.get("himalayas", "https://jobs.example.invalid/feed")

    monkeypatch.setattr(pipeline, "collect", fetch_unlisted)
    result = cli("scan", "--no-open", "--workspace", sample_workspace.root, *TODAY)
    assert_friendly(result, 1)
    assert "honestcv add-offer --paste" in result.stderr


def test_cli_friendly_errors_block_check(cli, sample_workspace):
    letter = sample_workspace.root / "letters" / "cover-letter-planted.md"
    before = tree(letter.parent)
    result = cli("check", sample_workspace.root / "letters" / "cover-letter-planted.md", *TODAY, "--workspace", sample_workspace.root)
    assert result.exit_code == 4, result.output
    assert "Traceback" not in result.output
    assert result.stdout.startswith("BLOCK · 4 lines blocked · 0 lines to review · 1 line passed")
    assert "Your file was not changed. Fix the lines yourself, then run the check again before sending." in result.stdout
    assert result.stdout.rstrip().endswith("Exit code 4 (BLOCK)")
    assert tree(letter.parent) == before


def test_cli_friendly_errors_internal(cli, sample_workspace, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE LEDGER LINE")

    monkeypatch.setattr(pipeline, "run_scan", broken)
    result = cli("scan", "--offline", "--no-open", "--workspace", sample_workspace.root)
    assert_friendly(result, 5)
    assert "This is a bug in honestcv. Run again with --debug and open an issue." in result.stderr
    assert "The output may contain lines from your CV or ledger. Remove them before posting." in result.stderr
    assert "PRIVATE LEDGER LINE" not in result.output
    debug = cli("scan", "--offline", "--no-open", "--workspace", sample_workspace.root, "--debug")
    assert isinstance(debug.exception, RuntimeError)


def test_doctor_outside_a_workspace_and_json(cli, schemas, validate, _isolated_home):
    outside = cli("doctor")
    assert outside.exit_code == 1
    assert any(line.startswith("ERROR workspace") for line in outside.stdout.splitlines())
    init_workspace(_isolated_home / "honestcv")
    inside = cli("doctor")
    assert "OK    workspace   ~/honestcv" in inside.stdout
    assert str(_isolated_home) not in inside.stdout
    as_json = cli("doctor", "--json")
    data = json.loads(as_json.stdout)
    assert validate(data, schemas["honestcv.doctor.v1"]) == []
    assert as_json.exit_code == (1 if data["errors"] else 0)


def test_add_offer_url_prints_the_unlisted_host_message(cli, workspace):
    result = cli("add-offer", "--url", "https://jobs.example.invalid/offer/77?utm_source=alert", "--title", "Data Analyst", "--workspace", workspace.root, *TODAY)
    assert result.exit_code == 0, result.output
    assert UNLISTED_HOST_MESSAGE in result.stdout
    saved = json.loads((workspace.inbox / "added-2026-10-10-data-analyst.json").read_text(encoding="utf-8"))
    assert saved == [{"title": "Data Analyst", "url": "https://jobs.example.invalid/offer/77"}]


def test_add_cv_prints_the_polish_letters_warning(cli, workspace, tmp_path, monkeypatch):
    """The PDF reader is replaced by one that returns badly decoded text, as a broken font mapping would."""
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% placeholder; the reader is replaced in this test\n")
    monkeypatch.setattr(prof, "_read_pdf", lambda path: ("Praca w firmie " + "�" * 12 + " koniec tekstu", 1))
    result = cli("add-cv", pdf, "--workspace", workspace.root)
    assert result.exit_code == 0, result.output
    assert prof.BAD_LETTERS in result.stdout


def test_add_cv_refuses_a_doc_file_before_copying(cli, workspace, tmp_path):
    doc = tmp_path / "cv.doc"
    doc.write_bytes(b"old word file")
    result = cli("add-cv", doc, "--workspace", workspace.root)
    assert_friendly(result, 1)
    assert "Save it as PDF or DOCX, then try again." in result.stderr
    assert list(workspace.cvs.iterdir()) == []


# ---- test_windows_safety ----


def test_windows_safety_names():
    assert safe_name("Specjalista ds. kadr (k/m)") == "specjalista-ds-kadr-k-m"
    for reserved in ("CON", "NUL", "COM1", "con", "Lpt1"):
        assert safe_name(reserved).lower() not in {"con", "nul", "com1", "lpt1"}


def test_windows_safety_utf8_stdout(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    cli_module._utf8()
    print("→ ≥ zażółć")
    stream.flush()
    assert raw.getvalue().decode("utf-8") == "→ ≥ zażółć\n"


def test_windows_safety_utf8_child_process(tmp_path, run_child):
    """Under a cp1252 output encoding (a redirect on Windows) the arrow still prints, as UTF-8."""
    home = tmp_path / "home"
    home.mkdir()
    init_workspace(tmp_path / "ws")
    result = run_child(["add-offer", "--text", "Analityk → ≥ danych", "--workspace", str(tmp_path / "ws"), *TODAY], cwd=tmp_path, home=home,
                       extra_env={"PYTHONIOENCODING": "cp1252"})  # fmt: skip
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "Analityk → ≥ danych" in result.stdout.decode("utf-8")


def test_windows_safety_unquoted_names_with_spaces(cli, workspace, samples_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CV Maya Lindqvist.md").write_bytes((samples_dir / "cvs" / "maya-analytics.md").read_bytes())
    joined = cli("add-cv", "CV", "Maya", "Lindqvist.md", "--workspace", workspace.root)
    assert joined.exit_code == 0, joined.output
    assert (workspace.cvs / "CV Maya Lindqvist.md").is_file()
    missing = cli("add-cv", "CV", "Maya", "Kowalska.md", "--workspace", workspace.root)
    assert_friendly(missing, 1)
    assert "If the file name has spaces, put it in quotes or drag the file into this window." in missing.stderr


def test_windows_safety_tilde_expands(cli, samples_dir, _isolated_home):
    init_workspace(_isolated_home / "honestcv")
    (_isolated_home / "maya.md").write_bytes((samples_dir / "cvs" / "maya-analytics.md").read_bytes())
    result = cli("add-cv", "~/maya.md", "--workspace", "~/honestcv")
    assert result.exit_code == 0, result.output
    assert (_isolated_home / "honestcv" / "cvs" / "maya.md").is_file()


# ---- test_default_workspace and test_init_idempotent ----


def test_default_workspace(cli, samples_dir, tmp_path, _isolated_home):
    outputs = []
    created = cli("init")
    outputs.append(created.stdout)
    assert created.exit_code == 0, created.output
    assert created.stdout.startswith("Created ~/honestcv/")
    assert created.stdout.rstrip().splitlines()[-1] == "Next: honestcv scan"
    assert (_isolated_home / "honestcv" / "honestcv.yml").is_file()
    found = cli("ledger", "lint")
    outputs.append(found.stdout)
    assert found.exit_code == 0 and found.stdout.startswith("ledger.yml: 0 claims")
    custom = cli("init", tmp_path / "elsewhere", "--cv", samples_dir / "cvs" / "maya-analytics.md")
    outputs.append(custom.stdout)
    assert "Read cvs/maya-analytics.md:" in custom.stdout and "about 10 years in total" in custom.stdout
    assert custom.stdout.rstrip().splitlines()[-1].startswith("Next: honestcv scan --workspace ")
    outputs.append(cli("add-cv", samples_dir / "cvs" / "maya-support-ops.md", "--workspace", tmp_path / "elsewhere").stdout)
    outputs.append(cli("ledger", "draft", "--workspace", tmp_path / "elsewhere", *TODAY).stdout)
    assert all("&&" not in text for text in outputs)


def test_init_idempotent(cli, tmp_path):
    root = tmp_path / "ws"
    assert cli("init", root).exit_code == 0
    assert (root / ".gitignore").read_text(encoding="utf-8") == "*\n"
    before = tree(root)
    again = cli("init", root)
    assert again.exit_code == 0 and "nothing was changed" in again.stdout
    assert tree(root) == before
    edited = (root / "honestcv.yml").read_text(encoding="utf-8") + "# my own note\n"
    (root / "honestcv.yml").write_text(edited, encoding="utf-8")
    (root / "cvs" / "cv.md").write_text("# A CV\n", encoding="utf-8")
    (root / "ledger.yml").write_text("# my ledger\nschema_version: 1\nperson: {}\nclaims: []\n", encoding="utf-8")
    forced = cli("init", root, "--force")
    assert forced.exit_code == 0, forced.output
    assert (root / "honestcv.yml.bak").read_text(encoding="utf-8") == edited
    assert (root / "honestcv.yml").read_text(encoding="utf-8").replace('csv_delimiter: ";"', 'csv_delimiter: ","') == default_config_text()
    assert (root / "cvs" / "cv.md").read_text(encoding="utf-8") == "# A CV\n"
    assert (root / "ledger.yml").read_text(encoding="utf-8").startswith("# my ledger")


# ---- the other screens: scan, tailor, add-offer --paste, schedule ----


def test_scan_partial_names_the_source_and_exits_3(cli, sample_workspace, monkeypatch):
    from honestcv.models import SourceStatus

    refused = SourceStatus(id="weworkremotely", state="refused", http_status=403, offers=0,
                           message="weworkremotely refused access (HTTP 403). honestcv does not work around this. Try later, or import offers by file.")  # fmt: skip
    monkeypatch.setattr(pipeline, "collect", lambda cfg, ws, *, offline, today: ([], [refused]))
    result = cli("scan", "--no-open", "--workspace", sample_workspace.root, *TODAY)
    assert result.exit_code == 3, result.output
    assert "Status    PARTIAL" in result.stdout and "weworkremotely: weworkremotely refused access (HTTP 403)" in result.stdout
    assert "Matches   out/matches.csv  +4 rows" in result.stdout


def test_scan_without_imported_offers_says_how_to_bring_them_in(cli, sample_workspace):
    (sample_workspace.inbox / "sample-offers.json").unlink()
    result = cli("scan", "--offline", "--no-open", "--workspace", sample_workspace.root, *TODAY)
    assert result.exit_code == 0, result.output
    assert cli_module.NO_IMPORTS in result.stdout


def test_scan_every_rejects_short_intervals(cli, sample_workspace):
    result = cli("scan", "--every", "10m", "--workspace", sample_workspace.root)
    assert_friendly(result, 1)


def test_tailor_screen(cli, sample_workspace):
    assert cli("scan", "--offline", "--no-open", "--workspace", sample_workspace.root, *TODAY).exit_code == 0
    result = cli("tailor", "1", "--workspace", sample_workspace.root, *TODAY)
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "Offer   Data Analyst · Tallgrove Analytics (83%, CORE, best CV maya-analytics.md)"
    assert lines[2] == "Picked  2 claims · 3 required skills have no ledger backing: excel, airflow, dbt (listed in gaps.md, added nowhere)"
    assert lines[3] == "Check   PASS (read back from disk)"
    assert lines[4] == "Wrote 2 bullets to paste into your CV"
    assert lines[5].strip() == "out/tailored/2026-10-10-tallgrove-analytics-data-analyst/"
    assert (sample_workspace.out / "tailored" / "2026-10-10-tallgrove-analytics-data-analyst" / "highlights.md").is_file()
    assert cli("tailor", "9", "--workspace", sample_workspace.root, *TODAY).exit_code == 1


def test_check_with_an_empty_ledger_stops(cli, workspace, samples_dir):
    result = cli("check", samples_dir / "letters" / "cover-letter-planted.md", "--workspace", workspace.root)
    assert_friendly(result, 1)
    assert "Your ledger is empty." in result.stderr and "honestcv ledger draft" in result.stderr


def test_add_offer_paste(cli, workspace, monkeypatch):
    from honestcv import importers

    monkeypatch.setattr(importers, "read_clipboard", lambda: "Analityk danych\nWymagania: SQL, Excel.\n")
    result = cli("add-offer", "--paste", "--workspace", workspace.root, *TODAY)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith('Imported 1 offer from the clipboard: "Analityk danych" (title from the first line). It joins the next scan.')
    assert (workspace.inbox / "added-2026-10-10-analityk-danych.json").is_file()
    both = cli("add-offer", "--paste", "--url", "https://jobs.example.invalid/1", "--workspace", workspace.root)
    assert_friendly(both, 1)


@pytest.mark.parametrize("platform, needle", [("windows", "schtasks /Create /SC DAILY /ST 08:00"), ("linux", "0 8 * * *"), ("macos", "launchctl bootstrap")])
def test_schedule_command_prints_and_writes_nothing(cli, workspace, platform, needle, _isolated_home):
    before = tree(workspace.root), tree(_isolated_home)
    result = cli("schedule", "--platform", platform, "--workspace", workspace.root)
    assert result.exit_code == 0, result.output
    assert needle in result.stdout and str(workspace.root) in result.stdout
    assert (tree(workspace.root), tree(_isolated_home)) == before


def test_sources_and_version(cli):
    version = cli("--version")
    assert version.stdout.strip() == "honestcv 0.1.0"
    table = cli("sources")
    assert table.exit_code == 0 and "| `himalayas` |" in table.stdout


def test_report_rerenders_the_latest_run(cli, sample_workspace):
    assert cli("scan", "--offline", "--no-open", "--workspace", sample_workspace.root, *TODAY).exit_code == 0
    (sample_workspace.out / "report-latest.html").unlink()
    result = cli("report", "--workspace", sample_workspace.root)
    assert result.exit_code == 0, result.output
    assert (sample_workspace.out / "report-latest.html").is_file()
    assert cli("report", "--run", "no-such-run", "--workspace", sample_workspace.root).exit_code == 1


def test_scan_twice_marks_offers_seen_and_appends_rows(cli, sample_workspace):
    first = cli("scan", "--offline", "--no-open", "--workspace", sample_workspace.root, *TODAY)
    second = cli("scan", "--offline", "--no-open", "--workspace", sample_workspace.root, *TODAY)
    assert "new since last scan 4" in first.stdout and "new since last scan 0" in second.stdout
    rows = (sample_workspace.out / "matches.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 1 + 2 * 4
    assert sorted(p.name for p in (sample_workspace.out / "runs").iterdir()) == ["2026-10-10T00-00-00", "2026-10-10T00-00-00-2"]
    ws = Workspace.for_root(sample_workspace.root)
    assert json.loads((ws.out / "latest.json").read_text(encoding="utf-8"))["new_matches"] == []


def test_init_picks_the_csv_delimiter_and_scan_uses_it(cli, tmp_path, samples_dir, monkeypatch):
    from honestcv import workspace as workspace_module

    monkeypatch.setattr(workspace_module, "locale_csv_delimiter", lambda: ";")
    root = tmp_path / "ws"
    assert cli("init", root, "--cv", samples_dir / "cvs" / "maya-analytics.md").exit_code == 0
    assert 'csv_delimiter: ";"' in (root / "honestcv.yml").read_text(encoding="utf-8")
    (root / "offers" / "inbox" / "sample-offers.json").write_bytes((samples_dir / "offers" / "inbox" / "sample-offers.json").read_bytes())
    assert cli("scan", "--offline", "--no-open", "--workspace", root, *TODAY).exit_code == 0
    header = (root / "out" / "matches.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header == "scan_date;run_id;match;layer;title;company;best_cv;source;url;new"


def test_check_refuses_formats_it_cannot_read(cli, sample_workspace, tmp_path):
    odt = tmp_path / "letter.odt"
    odt.write_bytes(b"not readable here")
    result = cli("check", odt, "--workspace", sample_workspace.root)
    assert_friendly(result, 1)
    assert "Save it as PDF or DOCX, then try again." in result.stderr
