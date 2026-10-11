"""Read-only health check: workspace, config, CVs, ledger, sources and the last run. It writes nothing."""

import datetime as dt
import json
import platform
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from honestcv import __version__
from honestcv.config import Config, load_config
from honestcv.errors import HonestError
from honestcv.ledger import lint_ledger, load_ledger
from honestcv.profile import load_cv_text
from honestcv.report.matches import plural
from honestcv.textutil import home_relative
from honestcv.workspace import Workspace, find_workspace

CV_SUFFIXES = {".md", ".txt", ".pdf", ".docx", ".doc", ".odt", ".pages", ".rtf", ".webarchive"}  # the last five get the "save as PDF" message


class DoctorCheck(BaseModel):
    name: str
    level: Literal["OK", "WARN", "ERROR", "SKIP", "INFO"]
    message: str


def _today() -> dt.date:
    return dt.date.today()


def _problem(exc: HonestError) -> str:
    """What happened, where (the line) and how to fix it, on one line."""
    text = (f"line {exc.line}: " if exc.line else "") + exc.message + (f" {exc.hint}" if exc.hint else "")
    return " ".join(text.split())


def _config(ws: Workspace) -> tuple[Config | None, DoctorCheck]:
    if not ws.config.is_file():
        return Config(), DoctorCheck(name="config", level="WARN", message=f"{ws.config.name} not found; defaults are used. Run: honestcv init --force")
    try:
        cfg = load_config(ws.config)
    except HonestError as exc:
        return None, DoctorCheck(name="config", level="ERROR", message=_problem(exc))
    total = sum(c.weight for c in cfg.scoring.criteria)
    return cfg, DoctorCheck(name="config", level="OK", message=f"{ws.config.name} valid · criteria weights sum to {total}")


def _cvs(ws: Workspace) -> DoctorCheck:
    files = sorted((p for p in ws.cvs.iterdir() if p.is_file() and p.suffix.lower() in CV_SUFFIXES), key=lambda p: p.name) if ws.cvs.is_dir() else []
    if not files:
        return DoctorCheck(name="cvs", level="ERROR", message="no CV found in cvs/. Put your CV there or run: honestcv add-cv FILE")
    readable, unreadable, warnings = [], [], []
    for path in files:
        try:
            cv = load_cv_text(path)
        except HonestError as exc:
            unreadable.append(f"{path.name}: {_problem(exc)}")
            continue
        except Exception as exc:  # a damaged file must not stop the health check
            unreadable.append(f"{path.name}: could not be read ({type(exc).__name__}). Export it again as PDF or DOCX.")
            continue
        readable.append(path.name + (f" {plural(cv.pages, 'page')}" if cv.pages else ""))
        warnings += [f"{path.name}: {w}" for w in cv.warnings]
    text = f"{len(readable)} readable ({', '.join(readable)})" if readable else "none readable"
    text += "".join(f"; {note}" for note in [*unreadable, *warnings])
    level = "ERROR" if not readable else "WARN" if unreadable or warnings else "OK"
    return DoctorCheck(name="cvs", level=level, message=text)


def _ledger(ws: Workspace) -> DoctorCheck:
    if not ws.ledger.is_file():
        return DoctorCheck(name="ledger", level="WARN", message="ledger.yml not found. Run: honestcv init, then: honestcv ledger draft")
    try:
        ledger = load_ledger(ws.ledger)
        if ledger is None:
            return DoctorCheck(name="ledger", level="WARN", message="ledger.yml has no claims yet. Run: honestcv ledger draft")
        issues = lint_ledger(ledger, ws.root, _today())
    except HonestError as exc:
        return DoctorCheck(name="ledger", level="ERROR", message=_problem(exc))
    errors, warns = [i for i in issues if i.severity == "ERROR"], [i for i in issues if i.severity == "WARN"]
    if errors:
        first = errors[0]
        where = f"line {first.line} " if first.line else ""
        return DoctorCheck(name="ledger", level="ERROR", message=f"{plural(len(errors), 'lint error')}; tailoring is blocked. First: {where}{first.message}")
    drafts = sum(c.draft for c in ledger.claims)
    notes = [f"{plural(drafts, 'draft claim')}; tailoring and the check accept them only word for word"] if drafts else []
    if len(warns) > drafts:
        notes.append(f"{plural(len(warns) - drafts, 'other warning')} (run: honestcv ledger lint)")
    if notes:
        return DoctorCheck(name="ledger", level="WARN", message="; ".join(notes))
    return DoctorCheck(name="ledger", level="OK", message=f"{plural(len(ledger.claims), 'claim')} · lint clean")


def _last_scan(ws: Workspace) -> tuple[str, str]:
    try:
        data = json.loads((ws.out / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "OK", "no scan yet"
    if not isinstance(data, dict):
        return "OK", "no scan yet"
    stamp = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})", str(data.get("run_id", "")))
    when = f"{stamp[1]} {stamp[2]}:{stamp[3]}" if stamp else str(data.get("today", ""))
    status = str(data.get("status", "unknown"))
    return ("OK" if status == "PASS" else "WARN"), f"last scan {status} {when}".strip() + ("" if status == "PASS" else " (the report names the failing source)")


def _sources(ws: Workspace, cfg: Config | None) -> DoctorCheck:
    if cfg is None:
        return DoctorCheck(name="sources", level="SKIP", message="fix honestcv.yml first")
    ids = list(dict.fromkeys(cfg.search.sources))
    c = cfg.search.companies
    ids += [kind for kind, names in (("greenhouse", c.greenhouse), ("lever", [*c.lever, *c.lever_eu]), ("ashby", c.ashby)) if names and kind not in ids]
    level, last = _last_scan(ws)
    try:
        from honestcv.sources import REGISTRY

        unknown = [i for i in ids if REGISTRY and i not in REGISTRY]
    except Exception:  # the registry belongs to the sources unit; a health check does not fail because of it
        unknown = []
    if unknown:
        last += f"; not a known source: {', '.join(unknown)} (honestcv sources lists them)"
        level = "WARN"
    return DoctorCheck(name="sources", level=level, message=f"{len(ids)} enabled · {last}")


def _network(cfg: Config | None, online: bool) -> DoctorCheck:
    if not online:
        return DoctorCheck(name="network", level="SKIP", message="run honestcv doctor --online to test the sources")
    if cfg is None:
        return DoctorCheck(name="network", level="SKIP", message="fix honestcv.yml first")
    from honestcv.sources import collect

    probe = cfg.model_copy(deep=True)
    probe.search.keywords = probe.search.keywords or ["analyst"]
    try:
        # A throwaway workspace takes the HTTP cache, so the real workspace stays untouched.
        with tempfile.TemporaryDirectory(prefix="honestcv-doctor-") as tmp:
            _, statuses = collect(probe, Workspace.for_root(Path(tmp)), offline=False, today=_today())
    except HonestError as exc:
        return DoctorCheck(name="network", level="WARN", message=_problem(exc))
    parts = [f"{s.id} {s.state}" + (f" (HTTP {s.http_status})" if s.http_status and s.http_status >= 400 else "") for s in statuses if s.id != "inbox"]
    failed = any(s.state in ("drift", "refused", "error") for s in statuses)
    return DoctorCheck(name="network", level="WARN" if failed else "OK", message=" · ".join(parts) or "no source is enabled")


def _os_name() -> str:
    system = platform.system()
    return f"macOS {platform.mac_ver()[0].split('.')[0]}" if system == "Darwin" else f"{system} {platform.release()}".strip()


def run_doctor(start: Path | None, *, online: bool, workspace: Path | None = None) -> list[DoctorCheck]:
    """Check the workspace the way a scan would use it. Writes nothing; the INFO line is the header the CLI prints as it stands."""
    header = DoctorCheck(name="version", level="INFO", message=f"honestcv {__version__} · Python {platform.python_version()} · {_os_name()}")
    try:
        ws = find_workspace(start, explicit=workspace)
    except HonestError as exc:
        return [header, DoctorCheck(name="workspace", level="ERROR", message=_problem(exc))]
    cfg, config_check = _config(ws)
    return [header, DoctorCheck(name="workspace", level="OK", message=home_relative(ws.root)), config_check, _cvs(ws), _ledger(ws),
            _sources(ws, cfg), _network(cfg, online)]  # fmt: skip


def doctor_exit_code(checks: list[DoctorCheck]) -> int:
    """0 when no check is an ERROR, else 1."""
    return 1 if any(c.level == "ERROR" for c in checks) else 0
