"""Copies the sample workspace to a temporary folder and runs scan and check on the sample date."""

import shutil
import tempfile
import textwrap
from pathlib import Path

from honestcv import __version__
from honestcv.checker import check_document, summary_line
from honestcv.config import load_config
from honestcv.errors import WorkspaceError
from honestcv.ledger import load_ledger
from honestcv.models import CheckResult, ScanRun
from honestcv.pipeline import run_scan
from honestcv.report.matches import GATE_ORDER, build_view
from honestcv.textutil import home_relative
from honestcv.workspace import Workspace

SAMPLES = Path(__file__).resolve().parent / "samples" / "en"
LETTER = "letters/cover-letter-planted.md"
GATE_SHORT = {"deny_title": "deny-list", "deny_term": "deny-list", "commission_only": "commission only", "unpaid_task": "unpaid task",
              "equity_only": "equity only", "expired": "expired", "stale": "too old", "no_content": "no content",
              "ledger_ceiling": "ledger ceiling"}  # fmt: skip


def gate_summary(gate_counts: dict[str, int]) -> str:
    """'commission only 1, deny-list 1': largest first, then in gate order."""
    merged: dict[str, int] = {}
    for code in sorted(gate_counts, key=GATE_ORDER.index):
        merged[GATE_SHORT[code]] = merged.get(GATE_SHORT[code], 0) + gate_counts[code]
    return ", ".join(f"{label} {n}" for label, n in sorted(merged.items(), key=lambda kv: -kv[1]))


def offer_table(run: ScanRun) -> list[str]:
    rows = [(c.offer.title + (f" · {c.offer.company}" if c.offer.company else ""), c.best) for c in build_view(run).cards]
    width = max([len(t) for t, _ in rows] + [40]) + 3
    out = [f"{'#':>3}  Match  Layer      {'Offer':<{width}}Best CV"]
    out += [f"{i:>3}  {r.match_pct:>3}%   {r.layer:<11}{t:<{width}}{r.cv_id}" for i, (t, r) in enumerate(rows, 1)]
    return out


def check_lines(result: CheckResult, indent: str = "  ") -> list[str]:
    """The summary line, then each flagged line with its rule codes and claim ids."""
    out = [indent + summary_line(result)]
    groups: dict[tuple[int, str], list] = {}
    for f in result.flags:
        groups.setdefault((f.line, f.quote), []).append(f)
    for (line, quote), flags in groups.items():
        codes = ", ".join(dict.fromkeys(f.rule for f in sorted(flags, key=lambda f: f.severity != "BLOCK")))
        claims = sorted({f.claim_id for f in flags if f.claim_id})
        head = f"{indent}line {line}  "
        out.append(f'{head}"{quote}"')
        body = codes + (f"  ({', '.join(claims)})" if claims else "")
        out += textwrap.wrap(body, 96, initial_indent=" " * len(head), subsequent_indent=" " * len(head), break_on_hyphens=False)
    return out


def prepare(out: Path | None) -> Workspace:
    """A copy of the sample workspace in `out` (new or empty), else in a new temporary folder."""
    if out is None:
        root = Path(tempfile.mkdtemp(prefix="honestcv-demo-"))
    else:
        root = Path(out).expanduser().resolve()
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise WorkspaceError(f"{root.name} already exists and is not an empty folder.", path=home_relative(root),
                                 hint="Pass --out with a new folder name, or leave --out away to use a temporary folder.")  # fmt: skip
    shutil.copytree(SAMPLES, root, dirs_exist_ok=True)
    return Workspace.for_root(root)


def run_demo(out: Path | None) -> tuple[Workspace, ScanRun, CheckResult, list[str]]:
    """Offline scan of the sample workspace and the check of the planted letter, both on the sample date."""
    ws = prepare(out)
    cfg = load_config(ws.config)
    today = cfg.demo_today
    if today is None:
        raise WorkspaceError("The sample settings have no demo_today.", hint="This is a bug in honestcv. Run again with --debug and open an issue.")
    run = run_scan(ws, cfg, offline=True, today=today, pinned=True)
    ledger = load_ledger(ws.ledger)
    if ledger is None:
        raise WorkspaceError("The sample ledger has no claims.", hint="This is a bug in honestcv. Run again with --debug and open an issue.")
    check = check_document(ws.root / LETTER, ledger, ws_root=ws.root, today=today)
    scored = len({o.id for o in run.offers} - {r.offer_id for r in run.results if r.gate})
    filtered = sum(run.gate_counts.values())
    lines = [
        f"honestcv {__version__} · demo · offline · no API keys · sample data only · sample date {today.isoformat()}",
        f"Workspace: {home_relative(ws.root)}/   (English sample: Maya Lindqvist, fictional)", "",
        f"Read {len(run.offers)} sample offers; scored {scored} against {len(run.cvs)} CV variants.", "",
        *offer_table(run),
        f"  Filtered out: {filtered}" + (f" ({gate_summary(run.gate_counts)})" if filtered else ""), "",
        "Match % = how much of the offer your CV covers, by your own criteria. Not a hire probability.", "",
        f"Claims check of a planted cover letter ({LETTER}):", *check_lines(check),
        f"Report: {home_relative(ws.out / 'report-latest.html')}",
        "Next: honestcv init --cv path/to/your-cv.pdf",
    ]  # fmt: skip
    return ws, run, check, lines
