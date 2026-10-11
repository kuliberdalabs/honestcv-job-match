"""The Markdown report: layout, ranking, escaping."""

import json
import re

import pytest

from honestcv.models import ScanRun
from honestcv.report.markdown import render_markdown


@pytest.fixture
def run(golden_dir) -> ScanRun:
    return ScanRun.model_validate(json.loads((golden_dir / "scan_run_sample.json").read_text(encoding="utf-8")))


def offer(run: ScanRun, offer_id: str):
    return next(o for o in run.offers if o.id == offer_id)


def test_markdown_render(run):
    md = render_markdown(run)
    assert md.startswith("# Offers for you, 2026-10-10\n")
    assert "Status: PARTIAL · 4 sources + inbox · 8 offers scored against 2 CVs · 6 shown · 3 new" in md
    assert "Match % = how much of this offer your CV covers, by the criteria in your honestcv.yml. It is not a hire probability." in md
    cards = re.findall(r"^## (\d+)\. (.+?) · (.+?) · (\d+)% · (CORE|STRETCH|DOWNSHIFT)( · NEW)?$", md, re.M)
    assert [(c[0], c[3]) for c in cards] == [("1", "83"), ("2", "68"), ("3", "64"), ("4", "62"), ("5", "61"), ("6", "59")]
    assert cards[0][1:3] == ("Data Analyst", "Tallgrove Analytics") and cards[0][5] == " · NEW"
    assert [c[4] for c in cards] == ["CORE", "STRETCH", "CORE", "DOWNSHIFT", "CORE", "CORE"]
    assert [bool(c[5]) for c in cards] == [True, False, False, False, True, True]
    assert md.count("Next: honestcv tailor ") == 6 and "Next: honestcv tailor 1\n" in md
    # the worked example: row 1 line by line
    assert "Remote (SE) · 40,000-48,000 SEK/month · via your file sample-offers.json · best CV: maya-analytics.md (maya-support-ops.md: 66%)" in md
    assert "| skills | 45 | 30.5 | 4 of 6 required skills, 1 of 2 nice-to-have. Missing: airflow, dbt, tableau. |" in md
    assert "| contract | 5 | n/a | no preference set, weight redistributed |" in md
    assert "| language | 5 | 5.3 |" in md
    assert "Penalties: none · Unknown: none" in md
    assert ("Backed by your ledger: 4 of 5 matched requirements (sql, looker studio: backlog-dashboard; python, pandas: churn-capstone). "
            "CV only: excel.") in md  # fmt: skip
    assert "Missing from your CV: airflow, dbt (required), tableau (nice-to-have)" in md
    assert "Listing: https://jobs.tallgrove.invalid/openings/data-analyst" in md
    assert "Only skills on honestcv's list are recognized" in md


def test_markdown_sections(run):
    md = render_markdown(run)
    assert "**PARTIAL: Some sources failed. Results from them are missing.**" in md
    assert "- weworkremotely: weworkremotely returned data honestcv could not read. Results from it are missing; update honestcv." in md
    assert "## Needs the full text (1)" in md
    assert "- Data Analyst · (company not stated) · ~33% (title only) · https://alerts.example.invalid/r/48213 · run: honestcv add-offer --paste" in md
    assert "## Filtered out (2)" in md
    assert '- Sales Representative · Tinwhistle Leads · commission only: "Pay: this role is commission-based only."' in md
    assert "- Revenue Reporting Analyst · Harrowmere Hotels · valid until 2026-10-01" in md
    assert "Below threshold" not in md and "Branch Office Coordinator" not in md
    assert "## Sources" in md and "- Your inbox files: 8 offers" in md
    assert "Generated locally by honestcv 0.1.0. Contains data from your CV; keep it private." in md
    # a gated, low-confidence or below-threshold offer never gets a ranked card
    ranked_titles = re.findall(r"^## \d+\. (.+?) · ", md, re.M)
    assert "Sales Representative" not in ranked_titles and "Branch Office Coordinator" not in ranked_titles


def test_markdown_all_adds_below_threshold(run):
    md = render_markdown(run, show_below=True)
    assert "## Below threshold (1)" in md
    assert "- Branch Office Coordinator · Example Bank · 37% · CORE" in md


def test_ties_and_best_cv(run):
    md = render_markdown(run)
    # offer 0b31598cd4 scores 61 for both CVs: the cv_id that sorts first is the best CV
    assert "best CV: maya-analytics.md (maya-support-ops.md: 61%)" in md
    # the Polish offer scores higher with the second CV, so that CV is the best one
    assert "best CV: maya-support-ops.md (maya-analytics.md: 41%)" in md


def test_markdown_escapes_offer_text(run):
    md = render_markdown(run)
    assert "<" not in md and ">" not in md.replace("&gt;", "")
    assert "<img" not in md and "![" not in md and "](" not in md
    assert "## 5. &lt;img src=https://tracker.invalid/p&gt; · Example & Co · 61% · CORE · NEW" in md
    assert "Listing: javascript:alert(1)" in md  # shown as text, never as a link
    assert "Apply: https://apply.example.invalid/job?id=7&ref=list" in md


def test_markdown_escapes_links_and_table_cells(run):
    run.offers[0].title = "[click](https://evil.invalid) ![x](https://tracker.invalid/q.png)"
    run.results[0].criteria[1].note = "a|b <b>bold</b>"
    md = render_markdown(run)
    assert "[click](" not in md and "![" not in md and "<b>" not in md
    assert "\\[click\\](https://evil.invalid)" in md
    assert "| a\\|b &lt;b&gt;bold&lt;/b&gt; |" in md


def test_a_link_cannot_smuggle_markdown_syntax(run):
    run.offers[0].url = "https://x.invalid/)![x](https://tracker.invalid/p.png)[y](javascript:alert(1))"
    md = render_markdown(run)
    assert "![" not in md and "](" not in md and "[y]" not in md
    listing = next(line for line in md.splitlines() if line.startswith("Listing: https://x.invalid/"))
    assert " " not in listing.removeprefix("Listing: ").rstrip() and "%5B" in listing
    run.offers[0].url = "https://jobs.example.invalid/a%20b?id=7&ref=list#top"
    assert "Listing: https://jobs.example.invalid/a%20b?id=7&ref=list#top" in render_markdown(run)


def test_markdown_never_says_no_matches_when_a_source_failed(run):
    run.results = []
    md = render_markdown(run)
    assert "No offers to show. Some sources failed, so this list may be incomplete." in md and "reached the threshold" not in md
    run.status = "FAIL"
    assert "**FAIL: Could not collect offers.**" in render_markdown(run)
    run.status, run.sources = "PASS", [s for s in run.sources if s.state in ("ok", "cached")]
    assert "No offer reached the threshold for its layer." in render_markdown(run)


def test_markdown_without_a_ledger(run):
    run.ledger_present = False
    assert "Backed by your ledger: no ledger yet. Run: honestcv ledger draft" in render_markdown(run)


def test_possible_duplicates_share_one_card(run):
    host = offer(run, "085450678e")
    twin = offer(run, "53650315c7")
    twin.possible_duplicate_of = host.id
    md = render_markdown(run)
    assert md.count("\n## ") >= 5 and "Analytics Engineer · Quarry Lane Software" not in "\n".join(re.findall(r"^## \d+\..*$", md, re.M))
    assert "Also listed: https://careers.quarrylane.invalid/jobs/analytics-engineer" in md
    assert "5 shown" in md


def test_render_is_repeatable_and_survives_a_json_round_trip(run):
    first = render_markdown(run, show_below=True)
    again = ScanRun.model_validate_json(run.model_dump_json(by_alias=True))
    assert render_markdown(run, show_below=True) == first == render_markdown(again, show_below=True)


def test_a_backslash_in_offer_text_cannot_undo_the_escape():
    import re

    from honestcv.report.markdown import esc

    out = esc("Data Analyst \\[click\\](https://attacker.example.invalid/phish) ![img](x) \\\\[two]")
    for m in re.finditer(r"[\[\]]", out):
        slashes = len(out[: m.start()]) - len(out[: m.start()].rstrip("\\"))
        assert slashes % 2 == 1, (out, m.start())  # every bracket is escaped, so no link or image can open

