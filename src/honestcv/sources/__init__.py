"""Job sources: collect() and the registry."""

import datetime as dt
from typing import Literal

from honestcv import __version__
from honestcv.config import Config
from honestcv.errors import ConfigError
from honestcv.models import Offer, SourceStatus
from honestcv.sources.arbeitnow import ArbeitnowAdapter
from honestcv.sources.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from honestcv.sources.base import Adapter, run_adapter
from honestcv.sources.himalayas import HimalayasAdapter
from honestcv.sources.http import HttpClient, load_registry
from honestcv.sources.remoteok import RemoteOkAdapter
from honestcv.sources.weworkremotely import WeWorkRemotelyAdapter
from honestcv.textutil import hit
from honestcv.workspace import Workspace

REGISTRY: dict[str, dict] = load_registry()  # from registry.yml
ADAPTERS: dict[str, type] = {a.id: a for a in (HimalayasAdapter, RemoteOkAdapter, WeWorkRemotelyAdapter, ArbeitnowAdapter,
                                              GreenhouseAdapter, LeverAdapter, AshbyAdapter)}  # fmt: skip
USER_AGENT = f"honestcv/{'.'.join(__version__.split('.')[:2])} (+https://github.com/kuliberdalabs/honestcv-job-match)"


def allowed_hosts() -> frozenset[str]:
    """Every host named in registry.yml. HttpClient refuses all others."""
    return frozenset(h.lower() for entry in REGISTRY.values() for h in entry["hosts"])


def _adapter(source_id: str, cfg: Config) -> Adapter:
    return HimalayasAdapter(cfg.preferences.countries) if source_id == "himalayas" else ADAPTERS[source_id]()


def collect(cfg: Config, ws: Workspace, *, offline: bool, today: dt.date) -> tuple[list[Offer], list[SourceStatus]]:
    """Run every enabled source: search.sources, plus Greenhouse, Lever and Ashby once companies are listed.

    Empty search.keywords falls back to profile.target_titles. Titles the CV reader found are never search terms.
    The inbox is not read here; see importers.import_inbox. Offers are not deduplicated here.
    """
    companies = cfg.search.companies
    keywords = list(cfg.search.keywords or cfg.profile.target_titles)
    search = cfg.search.model_copy(update={"keywords": keywords})
    chosen = list(dict.fromkeys(search.sources))
    unknown = [name for name in chosen if name not in ADAPTERS]
    if unknown:
        raise ConfigError(f"search.sources lists '{unknown[0][:30]}', which is not a source honestcv knows.", path="honestcv.yml",
                          hint=f"Valid names: {', '.join(ADAPTERS)}. Run: honestcv sources")  # fmt: skip
    for name, tokens in (("greenhouse", companies.greenhouse), ("lever", companies.lever + companies.lever_eu), ("ashby", companies.ashby)):
        if tokens and name not in chosen:
            chosen.append(name)
    http = HttpClient(ws.state_dir / "cache", offline=offline, user_agent=USER_AGENT, allowed_hosts=allowed_hosts())
    offers: list[Offer] = []
    statuses: list[SourceStatus] = []
    try:
        for source_id in chosen:
            found, status = run_adapter(_adapter(source_id, cfg), http, search, today)
            if REGISTRY[source_id].get("filter_keywords") and keywords and found:
                kept = [o for o in found if any(hit(f"{o.title}\n{o.description}".lower(), k) for k in keywords)]
                if kept:
                    head, sep, rest = status.message.partition("; ")  # the count first, then any skipped entries or problems
                    status = status.model_copy(update={"offers": len(kept), "message": f"{head}, {len(kept)} match your keywords{sep}{rest}"})
                else:
                    status = status.model_copy(update={"state": "empty", "offers": 0, "message": "no offers matched your keywords"})
                found = kept
            offers += found
            statuses.append(status)
    finally:
        http.close()
    return offers, statuses


def collection_status(statuses: list[SourceStatus], inbox_offers: int = 0) -> Literal["PASS", "PARTIAL", "FAIL"]:
    """PARTIAL when any source was refused, unreadable or failed (a robots.txt that could not be read included);
    FAIL when every source did and nothing came from the inbox either. A source that was deliberately skipped
    (offline, no cache) never makes a run PARTIAL."""
    failed = [s for s in statuses if s.state in ("refused", "drift", "error")]
    if failed and len(failed) == len(statuses) and not inbox_offers:
        return "FAIL"
    return "PARTIAL" if failed else "PASS"


def registry_table(statuses: list[SourceStatus] | None = None) -> str:
    """The registry as a Markdown table (docs/sources.md and `honestcv sources`); with statuses, a last-status column."""
    head = ["Source", "Default", "Verdict", "Terms", "Verified", "Min interval", "Cache", "Attribution"]
    last = {s.id: s for s in statuses or []}
    lines = []
    for source_id, entry in REGISTRY.items():
        row = [f"`{source_id}`", "on" if entry["default_on"] else "off until companies are listed", entry["verdict"],
               f"<{entry['terms_url']}>", str(entry["verified_at"]), f"{entry['min_interval_s']:g} s", f"{entry['cache_ttl_h']:g} h",
               entry["attribution"]]  # fmt: skip
        if statuses is not None:
            row.append(f"{last[source_id].state}: {last[source_id].message}" if source_id in last else "not run")
        lines.append("| " + " | ".join(row) + " |")
    if statuses is not None:
        head.append("Last status")
    return "\n".join(["| " + " | ".join(head) + " |", "|" + " --- |" * len(head), *lines]) + "\n"
