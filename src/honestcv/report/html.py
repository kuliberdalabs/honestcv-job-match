"""Self-contained HTML report (out/report-<date>.html): inline CSS, no script, no external requests."""

import html
from importlib import resources
from string import Template

from honestcv import __version__
from honestcv.models import ScanRun
from honestcv.report.matches import (
    FIXED_LINE, GATE_LABEL, SKILLS_NOTE, Card, also_text, backed_text, build_view, is_link, missing_text, penalty_texts, plural, points,
    salary_text, source_name, terms_url, where_text,
)  # fmt: skip


def h(value: object) -> str:
    """Every value that comes from an offer, a CV, a file name or the config goes through here."""
    return html.escape(str(value), quote=True)


def link(url: str | None, label: str) -> str:
    """A link for http and https addresses only; any other address (javascript:, data:) is printed as text."""
    if not is_link(url):
        return h(url or label)
    return f'<a href="{h(url)}" target="_blank" rel="noopener">{h(label)}</a>'


def _criteria(card: Card) -> str:
    rows = []
    for c in card.best.criteria:
        if not c.applicable:
            rows.append(f'<div class="crit na"><div class="row"><span>{h(c.id)}</span><span>n/a</span></div>'
                        f'<p class="why">{h(c.note.removeprefix("n/a: "))}</p></div>')  # fmt: skip
            continue
        width = round(100 * min(max(c.fraction, 0.0), 1.0))
        quotes = "".join(f"<blockquote>{h(q)}</blockquote>" for q in c.quotes)
        rows.append(f'<div class="crit"><div class="row"><span>{h(c.id)} <em>weight {c.weight}</em></span><span>{points(c.points)} points</span></div>'
                    f'<div class="bar"><i style="width:{width}%"></i></div><p class="why">{h(c.note)}</p>{quotes}</div>')  # fmt: skip
    return "".join(rows)


def _card(card: Card, ledger_present: bool) -> str:
    o, best = card.offer, card.best
    facts = [where_text(o), salary_text(o), f"via {o.attribution}"]
    links = [f"Listing: {link(o.url, f'View listing (via {o.attribution})')}"]
    if o.apply_url and o.apply_url != o.url:
        links.append(f"Apply: {link(o.apply_url, 'Apply page')}")
    links += [f"Also listed: {link(u, 'Same job elsewhere')}" for u in [*o.other_urls, *(d.url for d in card.dups if d.url)]]
    penalties = "; ".join(penalty_texts(best)) or "none"
    chips = f'<span class="chip">{best.layer}</span>' + (' <span class="chip new">NEW</span>' if card.new else "")
    other = f" Also scored: {h(also_text(card))}." if card.others else ""
    return (
        f'<article class="card"><div class="top"><div class="pct b-{best.band or "fair"}"><b>{best.match_pct}%</b><small>{best.band or ""}</small></div>'
        f'<div><h3><span class="rank">{card.rank}.</span>{h(o.title)} <span class="sub">· {h(o.company or "(company not stated)")}</span></h3>'
        f'<p class="meta">{" · ".join(h(f) for f in facts if f)}</p><p class="chips">{chips}</p></div></div>'
        f'<p class="cv">Best CV: {h(best.cv_id)}.{other}</p><p class="links">{" · ".join(links)}</p>'
        f"<details><summary>How {best.match_pct}% was scored</summary>{_criteria(card)}<p class=\"small\">{h(SKILLS_NOTE)}</p>"
        f"<dl><dt>Penalties</dt><dd>{h(penalties)}</dd><dt>Unknown</dt><dd>{h('; '.join(best.unknowns) or 'none')}</dd>"
        f"<dt>Backed by your ledger</dt><dd>{h(backed_text(best, ledger_present))}</dd>"
        f"<dt>Missing from your CV</dt><dd>{h(missing_text(best))}</dd></dl></details>"
        f"<p>Next: <code>honestcv tailor {card.rank}</code></p></article>"
    )  # fmt: skip


def render_html(run: ScanRun, *, show_below: bool = False) -> str:
    view = build_view(run)
    inbox = any(s.id == "inbox" for s in run.sources)
    summary = (f"{plural(sum(s.id != 'inbox' for s in run.sources), 'source')}{' + inbox' if inbox else ''} · "
               f"{plural(view.scored, 'offer')} scored against {plural(len(run.cvs), 'CV')} · {len(view.cards)} shown · {view.new} new"
               f" · CVs used: {', '.join(run.cvs) or 'none'}")  # fmt: skip
    banner = ""
    if run.status != "PASS" or view.failing:
        lead = "Could not collect offers." if run.status == "FAIL" else "Some sources failed. Results from them are missing."
        failed = "".join(f"<li><strong>{h(s.id)}</strong>: {h(s.message)}</li>" for s in view.failing)
        banner = f'<div class="banner{" fail" if run.status == "FAIL" else ""}" role="status"><p><strong>{h(run.status)}: {lead}</strong></p><ul>{failed}</ul></div>'
    body = ["<h2>Offers</h2>"]
    if not view.cards:
        body.append("<p>No offers to show. Some sources failed, so this list may be incomplete.</p>" if run.status != "PASS"
                    else "<p>No offer reached the threshold for its layer.</p>")  # fmt: skip
    body += [_card(c, run.ledger_present) for c in view.cards]
    if view.needs_text:
        items = "".join(f"<li>{h(o.title)} · {h(o.company or '(company not stated)')} · ~{r.match_pct}% ({'title only' if o.thin else 'too little detail to rank'})"
                        f" · {link(o.url, 'link')} · run: <code>honestcv add-offer --paste</code></li>" for o, r in view.needs_text)  # fmt: skip
        body.append(f"<h2>Needs the full text ({len(view.needs_text)})</h2><ul>{items}</ul>")
    if view.filtered:
        groups = []
        for code in dict.fromkeys(g.code for _, g in view.filtered):
            items = "".join(f"<li>{h(o.title)} · {h(o.company or '(company not stated)')} · {h(g.detail)}" + (f': "{h(g.quote)}"' if g.quote else "") + "</li>"
                            for o, g in view.filtered if g.code == code)  # fmt: skip
            groups.append(f"<h3>{h(GATE_LABEL[code])}</h3><ul>{items}</ul>")
        body.append(f"<details><summary>Filtered out ({len(view.filtered)})</summary>{''.join(groups)}</details>")
    if show_below and view.below:
        items = "".join(f"<li>{h(o.title)} · {h(o.company or '(company not stated)')} · {r.match_pct}% · {r.layer}</li>" for o, r in view.below)
        body.append(f"<h2>Below threshold ({len(view.below)})</h2><ul>{items}</ul>")
    sources = []
    for s in run.sources:
        terms = terms_url(s.id)
        sources.append(f"<li>{h(source_name(run, s))}: {plural(s.offers, 'offer')}" + (f", {link(terms, 'terms')}" if is_link(terms) else "")
                       + ("" if s.state == "ok" else f" ({h(s.state)}). {h(s.message)}") + "</li>")  # fmt: skip
    body.append(f"<h2>Sources</h2><ul>{''.join(sources)}</ul>")
    page = Template(resources.files("honestcv.report").joinpath("template.html").read_text(encoding="utf-8"))
    return page.substitute(
        title=h(f"Offers for you, {run.today.isoformat()}"), status=h(run.status), status_class=f"s-{run.status.lower()}", summary=h(summary),
        banner=banner, fixed=h(FIXED_LINE), body="\n".join(body),
        footer=h(f"Generated locally by honestcv {__version__}. Contains data from your CV; keep it private."),
    )  # fmt: skip
