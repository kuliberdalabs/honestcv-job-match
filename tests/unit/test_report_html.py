"""The HTML report: self-contained, escaped, no script and no external request."""

import html
import json
import re
from html.parser import HTMLParser

import pytest

import honestcv.sources
from honestcv.models import ScanRun
from honestcv.report.html import render_html

CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; img-src 'none'; "
       "base-uri 'none'; form-action 'none'")  # fmt: skip
URL = re.compile(r"https?://[^\s\"'<>]+")


@pytest.fixture
def run(golden_dir) -> ScanRun:
    return ScanRun.model_validate(json.loads((golden_dir / "scan_run_sample.json").read_text(encoding="utf-8")))


@pytest.fixture(autouse=True)
def no_registry(monkeypatch):
    """Terms links come from the sources registry; these tests look at the report alone."""
    monkeypatch.setattr(honestcv.sources, "REGISTRY", {})


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def tags_of(page: str) -> list[tuple[str, dict]]:
    parser = Tags()
    parser.feed(page)
    return parser.tags


def test_html_render(run):
    page = render_html(run)
    assert page.startswith("<!doctype html>")
    for banned in ("<script", "<link", "@import", "url(", "<iframe", "<form", "<img", "<object", "<embed"):
        assert banned not in page
    assert f'<meta http-equiv="Content-Security-Policy" content="{CSP}">' in page
    assert '<meta name="robots" content="noindex">' in page
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page
    assert "prefers-color-scheme: dark" in page and ":root {" in page
    assert "Generated locally by honestcv 0.1.0. Contains data from your CV; keep it private." in page
    assert "It is not a hire probability." in page
    assert page.count('<article class="card">') == 6
    assert '<span class="status s-partial">PARTIAL</span>' in page
    assert "4 sources + inbox · 8 offers scored against 2 CVs · 6 shown · 3 new" in page


def test_html_links_follow_the_source_terms(run):
    page = render_html(run)
    assert "nofollow" not in page and "noreferrer" not in page
    anchors = [attrs for tag, attrs in tags_of(page) if tag == "a"]
    assert anchors and all(a["rel"] == "noopener" and a["target"] == "_blank" for a in anchors)
    assert all(re.match(r"https?://", a["href"]) for a in anchors)
    assert "https://jobs.tallgrove.invalid/openings/data-analyst" in {a["href"] for a in anchors}
    assert "View listing (via your file sample-offers.json)" in page


def test_partial_banner_names_the_failing_source(run):
    page = render_html(run)
    banner = re.search(r'<div class="banner".*?</div>', page, re.S).group(0)
    assert "<strong>weworkremotely</strong>" in banner and "could not read" in banner and "PARTIAL" in banner
    run.status = "FAIL"
    assert 'class="banner fail"' in render_html(run) and "Could not collect offers." in render_html(run)
    run.status, run.sources = "PASS", [s for s in run.sources if s.state == "ok"]
    assert 'class="banner' not in render_html(run)


def test_not_applicable_criteria_are_labelled(run):
    page = render_html(run)
    assert '<div class="crit na"><div class="row"><span>contract</span><span>n/a</span></div><p class="why">no preference set, weight redistributed</p></div>' in page
    assert "<span>skills <em>weight 45</em></span><span>30.5 points</span>" in page
    assert '<div class="bar"><i style="width:64%"></i></div>' in page  # the bar is the share of the criterion met


def test_needs_the_full_text_lists_the_low_confidence_offer(run):
    page = render_html(run)
    section = page.split("<h2>Needs the full text (1)</h2>")[1].split("</ul>")[0]
    assert "Data Analyst" in section and "(company not stated)" in section and "~33% (title only)" in section
    assert "<code>honestcv add-offer --paste</code>" in section and 'href="https://alerts.example.invalid/r/48213"' in section
    # it is never ranked: none of the cards is the title-only offer
    cards = re.findall(r"<article.*?</article>", page, re.S)
    assert len(cards) == 6 and not any("company not stated" in card for card in cards)


def test_filtered_out_is_grouped_by_gate_in_a_collapsed_block(run):
    page = render_html(run)
    block = re.search(r"<details><summary>Filtered out \(2\)</summary>(.*?)</details>", page, re.S).group(1)
    assert block.index("Commission only") < block.index("Expired")
    assert 'commission only: "Pay: this role is commission-based only."' in block.replace("&quot;", '"')
    assert "valid until 2026-10-01" in block
    assert "Below threshold" not in page and "Below threshold (1)" in render_html(run, show_below=True)


def test_injection_fixture_is_plain_text(run):
    page = render_html(run)
    assert "<img src=https://tracker.invalid/p>" not in page
    assert "&lt;img src=https://tracker.invalid/p&gt;" in page
    assert 'href="javascript:' not in page and not any(a.get("href", "").startswith("javascript:") for _, a in tags_of(page))
    assert "javascript:alert(1)" in page  # printed as text
    assert "Example &amp; Co" in page
    assert 'href="https://apply.example.invalid/job?id=7&amp;ref=list"' in page
    run.results[0].criteria[2].quotes = ["<script>alert(1)</script> <b onmouseover=x>"]
    page = render_html(run)
    assert "<script" not in page and "<b " not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    names = {name for _, attrs in tags_of(page) for name in attrs}
    assert not any(name.startswith("on") for name in names)
    assert {tag for tag, _ in tags_of(page)} <= {"html", "head", "meta", "title", "style", "body", "main", "header", "h1", "h2", "h3", "p",
                                                 "span", "div", "a", "article", "small", "b", "em", "strong", "details", "summary", "i", "blockquote",
                                                 "dl", "dt", "dd", "ul", "li", "code", "footer"}  # fmt: skip


def test_html_self_contained(run, golden_dir):
    """The only http strings in the page are offer links from the fixture; none is a request the browser would make."""
    page = html.unescape(render_html(run, show_below=True))
    allowed = set()
    for o in run.offers:
        for text in (o.url, o.apply_url, o.title, o.description, *o.other_urls):
            allowed.update(URL.findall(text or ""))
    found = set(URL.findall(page))
    assert found and found <= allowed, found - allowed
    # and the browser fetches none of them: only <a href> carries an address
    for tag, attrs in tags_of(render_html(run)):
        for name in ("src", "srcset", "data", "poster", "action", "formaction", "background"):
            assert name not in attrs
        if tag != "a":
            assert "href" not in attrs


def test_terms_links_come_from_the_registry_when_it_has_them(run, monkeypatch):
    monkeypatch.setattr(honestcv.sources, "REGISTRY", {"himalayas": {"terms_url": "https://himalayas.invalid/terms"}, "remoteok": {}})
    page = render_html(run)
    assert '<li>Himalayas: 2 offers, <a href="https://himalayas.invalid/terms" target="_blank" rel="noopener">terms</a></li>' in page
    assert "https://himalayas.invalid/terms" in {a["href"] for t, a in tags_of(page) if t == "a"}
    monkeypatch.setattr(honestcv.sources, "REGISTRY", {"himalayas": {"terms_url": "javascript:alert(1)"}})
    page = render_html(run)
    assert 'href="javascript:' not in page and "terms</a>" not in page


def test_possible_duplicates_share_one_card(run):
    next(o for o in run.offers if o.id == "53650315c7").possible_duplicate_of = "085450678e"
    page = render_html(run)
    assert page.count('<article class="card">') == 5
    assert 'Also listed: <a href="https://careers.quarrylane.invalid/jobs/analytics-engineer"' in page


def test_html_is_repeatable_and_survives_a_json_round_trip(run):
    first = render_html(run, show_below=True)
    again = ScanRun.model_validate_json(run.model_dump_json(by_alias=True))
    assert render_html(again, show_below=True) == first == render_html(run, show_below=True)
