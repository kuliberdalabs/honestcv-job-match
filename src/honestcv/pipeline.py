"""Scan orchestration: collect, dedupe, score, save scan.json, render the reports, matches.csv and latest.json."""

import datetime as dt
import json

from honestcv.config import Config, config_digest
from honestcv.errors import WorkspaceError
from honestcv.importers import import_inbox
from honestcv.ledger import build_index, load_ledger
from honestcv.models import Profile, ScanRun
from honestcv.profile import load_profiles
from honestcv.report.html import render_html
from honestcv.report.markdown import render_markdown
from honestcv.report.matches import append_matches_csv, latest_json
from honestcv.scoring import score_all
from honestcv.sources import collect, collection_status
from honestcv.state import SeenStore, dedupe
from honestcv.workspace import Workspace, atomic_write, run_lock


def search_keywords(cfg: Config, profiles: list[Profile]) -> list[str]:
    """search.keywords, else the target titles followed by the top 5 skills across all CVs (titles first,
    because Himalayas searches only the first three keywords)."""
    if cfg.search.keywords:
        return list(cfg.search.keywords)
    first: dict[str, int] = {}
    for p in profiles:
        for i, skill in enumerate(p.skills):
            first[skill] = min(first.get(skill, i), i)
    titles = [t for p in profiles for t in p.target_titles]
    return list(dict.fromkeys([*titles, *sorted(first, key=lambda s: (first[s], s))[:5]]))


def _run_id(ws: Workspace, today: dt.date, pinned: bool) -> str:
    base = f"{today.isoformat()}T00-00-00" if pinned else dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_id, n = base, 2
    while (ws.out / "runs" / run_id).exists():
        run_id, n = f"{base}-{n}", n + 1
    return run_id


def write_reports(run: ScanRun, ws: Workspace, *, show_below: bool = False) -> None:
    """out/report-<date>.md|.html and the report-latest copies, each replaced atomically."""
    md, page = render_markdown(run, show_below=show_below), render_html(run, show_below=show_below)
    for name in (run.today.isoformat(), "latest"):
        atomic_write(ws.out / f"report-{name}.md", md)
        atomic_write(ws.out / f"report-{name}.html", page)


def load_run(ws: Workspace, run_id: str | None = None) -> ScanRun:
    """A saved run: the one named, else the newest one, which out/latest.json points to."""
    try:
        if run_id is None:
            run_id = str(json.loads((ws.out / "latest.json").read_text(encoding="utf-8"))["run_id"])
        return ScanRun.model_validate_json((ws.out / "runs" / run_id / "scan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        what = f"The saved run {run_id} was not found" if run_id else "There is no saved scan in this workspace yet"
        raise WorkspaceError(f"{what}.", hint="Run: honestcv scan") from None


def run_scan(ws: Workspace, cfg: Config, *, offline: bool, today: dt.date, pinned: bool = False, show_below: bool = False) -> ScanRun:
    """One scan. pinned (demo, --today) gives the run id <today>T00-00-00; otherwise it is the local start time."""
    with run_lock(ws):
        store = SeenStore.load(ws.state_dir / "state.json", today)
        profiles = load_profiles(ws, cfg, today=today)
        if not profiles:
            raise WorkspaceError("There is no CV in cvs/.", hint="Put your CV into cvs/ or run: honestcv add-cv FILE", path="cvs/")
        ledger = load_ledger(ws.ledger)
        index = build_index(ledger)
        search = cfg.model_copy(update={"search": cfg.search.model_copy(update={"keywords": search_keywords(cfg, profiles)})})
        collected, statuses = collect(search, ws, offline=offline, today=today)
        inbox, inbox_status = import_inbox(ws, store, today)
        offers = sorted(dedupe(collected + inbox), key=lambda o: o.id)
        results = score_all(offers, profiles, cfg, index, today)
        gates = {r.offer_id: r.gate.code for r in results if r.gate}
        surfaced = {r.offer_id for r in results if r.surfaced}
        counts: dict[str, int] = {}
        for code in gates.values():
            counts[code] = counts.get(code, 0) + 1
        run = ScanRun(
            run_id=_run_id(ws, today, pinned), today=today, status=collection_status([*statuses, inbox_status], len(inbox)),
            sources=[*statuses, inbox_status], cvs=[p.cv_id for p in profiles], offers=offers, results=results,
            gate_counts=dict(sorted(counts.items())), new_offer_ids=sorted(o.id for o in offers if store.is_new(o.id)),
            config_digest=config_digest(cfg), ledger_present=ledger is not None,
        )  # fmt: skip
        atomic_write(ws.out / "runs" / run.run_id / "scan.json", run.model_dump_json(by_alias=True, indent=1) + "\n")
        write_reports(run, ws, show_below=show_below)
        append_matches_csv(run, ws.out / "matches.csv", delimiter=cfg.output.csv_delimiter)
        atomic_write(ws.out / "latest.json", latest_json(run, ws))
        for o in offers:
            store.mark(o.id, "filtered" if o.id in gates else "surfaced" if o.id in surfaced else "below_threshold")
        store.save()
    return run
