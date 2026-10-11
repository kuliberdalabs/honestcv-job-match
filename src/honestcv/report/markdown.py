"""Markdown report (out/report-<date>.md)."""

import re
from urllib.parse import quote

from honestcv import __version__
from honestcv.models import ScanRun
from honestcv.report.matches import (
    FIXED_LINE, SKILLS_NOTE, also_text, backed_text, build_view, is_link, missing_text, penalty_texts, plural, points, salary_text,
    source_name, terms_url, where_text,
)  # fmt: skip


def esc(value: object) -> str:
    """One line of plain text for a Markdown viewer: no tags, no links and no images can come out of offer text."""
    text = " ".join(str(value).split()).replace("\\", "\\\\")  # a backslash in the text first, so it cannot undo an escape
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("[", "\\[").replace("]", "\\]")


def cell(value: object) -> str:
    return esc(value).replace("|", "\\|")


def link(url: str | None) -> str:
    """http and https addresses are printed bare, with the characters that open Markdown syntax percent-encoded."""
    return quote(url, safe=":/?#&=%+@;,~-._") if url and is_link(url) else esc(url or "")


def render_markdown(run: ScanRun, *, show_below: bool = False) -> str:
    view = build_view(run)
    inbox = any(s.id == "inbox" for s in run.sources)
    sources = plural(sum(s.id != "inbox" for s in run.sources), "source") + (" + inbox" if inbox else "")
    out = [f"# Offers for you, {run.today.isoformat()}", "",
           f"Status: {run.status} · {sources} · {plural(view.scored, 'offer')} scored against {plural(len(run.cvs), 'CV')} · "
           f"{len(view.cards)} shown · {view.new} new", FIXED_LINE, ""]  # fmt: skip
    if run.status != "PASS" or view.failing:
        lead = "Could not collect offers." if run.status == "FAIL" else "Some sources failed. Results from them are missing."
        out += [f"**{run.status}: {lead}**", ""] + [f"- {esc(s.id)}: {esc(s.message)}" for s in view.failing] + [""]
    if not view.cards:
        out += ["No offers to show. Some sources failed, so this list may be incomplete." if run.status != "PASS"
                else "No offer reached the threshold for its layer.", ""]  # fmt: skip
    for card in view.cards:
        o, best = card.offer, card.best
        out.append(f"## {card.rank}. {esc(o.title)} · {esc(o.company or '(company not stated)')} · {best.match_pct}% · {best.layer}"
                   + (" · NEW" if card.new else ""))  # fmt: skip
        facts = [where_text(o), salary_text(o), f"via {o.attribution}", f"best CV: {best.cv_id}" + (f" ({also_text(card)})" if card.others else "")]
        out.append(" · ".join(esc(f) for f in facts if f) + "  ")
        out.append(f"Listing: {link(o.url)}" + (f" · Apply: {link(o.apply_url)}" if o.apply_url and o.apply_url != o.url else "") + "  ")
        for extra in [*o.other_urls, *(d.url for d in card.dups if d.url)]:
            out.append(f"Also listed: {link(extra)}  ")
        out += ["", "| Criterion | Weight | Points | Why |", "|---|---:|---:|---|"]
        for c in best.criteria:
            shown, why = (points(c.points), c.note) if c.applicable else ("n/a", re.sub(r"^n/a:\s*", "", c.note))
            out.append(f"| {cell(c.id)} | {c.weight} | {shown} | {cell(why)} |")
        penalties = penalty_texts(best)
        out += ["", SKILLS_NOTE,
                f"Penalties: {esc('; '.join(penalties)) if penalties else 'none'} · Unknown: {esc('; '.join(best.unknowns)) or 'none'}  ",
                f"Backed by your ledger: {esc(backed_text(best, run.ledger_present))}  ",
                f"Missing from your CV: {esc(missing_text(best))}  ", f"Next: honestcv tailor {card.rank}", ""]  # fmt: skip
    if view.needs_text:
        out.append(f"## Needs the full text ({len(view.needs_text)})")
        for o, r in view.needs_text:
            kind = "title only" if o.thin else "too little detail to rank"
            out.append(f"- {esc(o.title)} · {esc(o.company or '(company not stated)')} · ~{r.match_pct}% ({kind}) · {link(o.url)}"
                       " · run: honestcv add-offer --paste")  # fmt: skip
        out.append("")
    if view.filtered:
        out.append(f"## Filtered out ({len(view.filtered)})")
        for o, gate in view.filtered:
            out.append(f"- {esc(o.title)} · {esc(o.company or '(company not stated)')} · {esc(gate.detail)}"
                       + (f': "{esc(gate.quote)}"' if gate.quote else ""))  # fmt: skip
        out.append("")
    if show_below and view.below:
        out.append(f"## Below threshold ({len(view.below)})")
        out += [f"- {esc(o.title)} · {esc(o.company or '(company not stated)')} · {r.match_pct}% · {r.layer}" for o, r in view.below]
        out.append("")
    out.append("## Sources")
    for s in run.sources:
        terms = terms_url(s.id)
        out.append(f"- {esc(source_name(run, s))}: {plural(s.offers, 'offer')}" + (f", terms {link(terms)}" if is_link(terms) else "")
                   + ("" if s.state == "ok" else f" ({s.state}). {esc(s.message)}"))  # fmt: skip
    out += ["", f"Generated locally by honestcv {__version__}. Contains data from your CV; keep it private.", ""]
    return "\n".join(out)
