"""Deterministic tailoring: ledger sentences only, checked from disk, blocked lines removed."""

import datetime as dt
import re
import shutil
from pathlib import Path

import pytest

from honestcv import checker
from honestcv.checker import strip_markers
from honestcv.errors import LedgerError
from honestcv.ledger import load_ledger
from honestcv.models import Claim, MatchResult, Offer, Profile, Requirement
from honestcv.tailor import select_claims, tailor
from honestcv.workspace import Workspace

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "src" / "honestcv" / "samples" / "en"
TODAY = dt.date(2026, 10, 10)
FALLBACK_SKILLS = ["sql", "python", "looker studio", {"name": "excel", "ambiguous": True}, "pandas", "scikit-learn",
                   "google sheets", "zendesk", "customer support", "airflow", "dbt", "tableau"]  # fmt: skip


@pytest.fixture(autouse=True)
def skills_vocabulary(monkeypatch):
    """data/skills.yml when it exists, else a short fallback term list."""
    if checker._skills_source() is None:
        monkeypatch.setattr(checker, "_skills_source", lambda: FALLBACK_SKILLS)
    checker.vocab.cache_clear()
    yield
    checker.vocab.cache_clear()


@pytest.fixture
def ws(tmp_path) -> Workspace:
    root = tmp_path / "sample"
    shutil.copytree(SAMPLES, root)
    return Workspace.for_root(root)


OFFER = Offer(id="a1b2c3d4e5", source="import:json", title="Data Analyst", company="Tallgrove Analytics",
              attribution="your file sample-offers.json", remote="remote", countries=["SE"],
              skills=["sql", "python", "looker studio", "excel", "airflow", "dbt"], skills_nice=["pandas", "tableau"],
              description="Requirements\n- 3+ years of experience\n\nFluent English required.")  # fmt: skip
REQS = [("sql", True, ["backlog-dashboard"]), ("python", True, ["churn-capstone"]), ("looker studio", True, ["backlog-dashboard"]),
        ("excel", True, []), ("airflow", True, []), ("dbt", True, []), ("pandas", False, ["churn-capstone"]), ("tableau", False, [])]  # fmt: skip
MATCH = MatchResult(offer_id=OFFER.id, cv_id="maya-analytics.md", gate=None, layer="CORE", layer_assumed=False, criteria=[],
                    penalties=[], base=83.08, match_pct=83, unknowns=[], requirements_recognized=8, backed=(4, 5),
                    surfaced=True, low_confidence=False, band="strong",
                    requirements=[Requirement(term=t, must=m, in_profile=t not in ("airflow", "dbt", "tableau"), backed_by=b) for t, m, b in REQS])  # fmt: skip
PROFILE = Profile(cv_id="maya-analytics.md", cv_sha256="0" * 64, skills=["sql", "python"], titles=["data analyst"],
                  target_titles=["data analyst"], years=6, level="mid", languages={"en": "C1", "sv": "native"})  # fmt: skip


def test_select_claims_worked_example():
    picks = select_claims(OFFER, MATCH, load_ledger(SAMPLES / "ledger.yml"))
    assert [c.id for c, s in picks] == ["backlog-dashboard", "churn-capstone"]
    assert all(s in c.allowed for c, s in picks)


def test_select_claims_skips_ceilings_hidden_and_reworded_drafts():
    ledger = load_ledger(SAMPLES / "ledger.yml")
    ledger.claims[0].visibility = "never"
    ledger.claims[2].draft = True
    ledger.claims[2].allowed = ["Built a churn model in Python and pandas."]  # no longer the CV line in summary
    match = MATCH.model_copy(update={"requirements": [*MATCH.requirements, Requirement(term="production", must=True, in_profile=False)]})
    assert select_claims(OFFER, match, ledger) == []


def test_select_claims_caps_at_eight_sentences():
    ledger = load_ledger(SAMPLES / "ledger.yml")
    ledger.claims[0].allowed = [f"Built the weekly ticket-backlog dashboard in SQL, version {n}." for n in range(10)]
    assert len(select_claims(OFFER, MATCH, ledger)) == 8


def test_tailor_deterministic(ws):
    ledger = load_ledger(ws.ledger)
    result = tailor(OFFER, MATCH, PROFILE, ledger, ws, today=TODAY)
    out = ws.root / result.out_dir
    assert result.out_dir == "out/tailored/2026-10-10-tallgrove-analytics-data-analyst"
    assert sorted(p.name for p in out.iterdir()) == ["check.md", "gaps.md", "highlights.cited.md", "highlights.md"]
    cited = (out / "highlights.cited.md").read_text(encoding="utf-8")
    bullets = [line for line in cited.split("\n") if line.startswith("- ")]
    allowed = {s for c in ledger.claims for s in c.allowed}
    assert [strip_markers(b)[2:] for b in bullets] == [ledger.claims[0].allowed[0], ledger.claims[2].allowed[0]]
    assert all(strip_markers(b)[2:] in allowed and "<!-- c:" in b for b in bullets)
    assert cited.startswith("# Highlights for Data Analyst · Tallgrove Analytics\n")
    assert result.check.verdict == "PASS" and result.rounds == 1 and result.removed == []
    assert (out / "highlights.md").read_text(encoding="utf-8") == strip_markers(cited)
    assert "c:" not in (out / "highlights.md").read_text(encoding="utf-8")
    assert result.gaps == ["excel", "airflow", "dbt"]
    gaps = (out / "gaps.md").read_text(encoding="utf-8")
    assert "Not added anywhere" in gaps and "- airflow" in gaps
    assert (out / "check.md").read_text(encoding="utf-8").startswith("# Claims check of highlights.cited.md\n\nPASS")


def test_tailor_checks_the_file_on_disk_and_removes_blocked_lines(ws, monkeypatch):
    real = checker.check_document
    calls = []

    def hook(path, *args, **kwargs):  # edit the written file before the first check reads it
        if not calls:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("- I deployed our churn model to production. <!-- c:churn-capstone -->\n")
        calls.append(path.read_text(encoding="utf-8"))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(checker, "check_document", hook)
    result = tailor(OFFER, MATCH, PROFILE, load_ledger(ws.ledger), ws, today=TODAY)
    out = ws.root / result.out_dir
    assert result.rounds == 2 and len(calls) == 2
    assert [(n, t) for n, t, r in result.removed] == [(5, "I deployed our churn model to production.")]
    assert result.removed[0][2] == "CEILING_BREACH"
    assert result.check.verdict == "PASS"
    assert "deployed" not in (out / "highlights.cited.md").read_text(encoding="utf-8")
    assert "deployed" not in (out / "highlights.md").read_text(encoding="utf-8")
    report = (out / "check.md").read_text(encoding="utf-8")
    assert 'Removed before writing highlights.md:\n  line 5  "I deployed our churn model to production."  CEILING_BREACH' in report


def test_tailor_is_repeatable(ws):
    ledger = load_ledger(ws.ledger)
    first = tailor(OFFER, MATCH, PROFILE, ledger, ws, today=TODAY)
    text = (ws.root / first.out_dir / "highlights.cited.md").read_text(encoding="utf-8")
    second = tailor(OFFER, MATCH, PROFILE, ledger, ws, today=TODAY)
    assert (ws.root / second.out_dir / "highlights.cited.md").read_text(encoding="utf-8") == text
    assert first.model_dump() == second.model_dump()


def test_tailor_is_blocked_by_lint_errors(ws):
    ledger = load_ledger(ws.ledger)
    ledger.claims[0].confirmed["shipped"][0].type = "file"
    with pytest.raises(LedgerError) as err:
        tailor(OFFER, MATCH, PROFILE, ledger, ws, today=TODAY)
    assert "Tailoring is blocked" in err.value.message and "honestcv ledger lint" in err.value.hint
    assert not (ws.out / "tailored").exists()


def test_a_skills_line_is_not_a_highlight():
    ledger = load_ledger(SAMPLES / "ledger.yml")
    line = "Skills: SQL, Python, Looker Studio, Excel"
    ledger.claims.append(Claim(id="skills-line", kind="skill", summary=line, skills=["sql", "python", "looker studio", "excel"],
                               allowed=[line], draft=True))  # fmt: skip
    assert [c.id for c, s in select_claims(OFFER, MATCH, ledger)] == ["backlog-dashboard", "churn-capstone"]


def test_offer_text_cannot_add_links_or_images(ws):
    image = "![track](https://attacker.example.invalid/pixel)"
    offer = OFFER.model_copy(update={"title": f"Data Analyst {image}", "company": "Tallgrove [Analytics](https://attacker.example.invalid/x) <img src=x>",
                                     "description": f"Requirements\n- Experience deploying ML models to production {image}."})  # fmt: skip
    match = MATCH.model_copy(update={"requirements": [*MATCH.requirements, Requirement(term=f"<b>dbt</b> {image}", must=True, in_profile=False)]})
    result = tailor(offer, match, PROFILE, load_ledger(ws.ledger), ws, today=TODAY)
    out = ws.root / result.out_dir
    assert sorted(p.name for p in out.iterdir()) == ["check.md", "gaps.md", "highlights.cited.md", "highlights.md"]
    for path in out.iterdir():
        text = strip_markers(path.read_text(encoding="utf-8"))
        assert not re.search(r"(?<!\\)[\[\]<]", text), (path.name, text)  # no tag, link or image can open
    assert (out / "highlights.md").read_text(encoding="utf-8").startswith("# Highlights for Data Analyst !\\[track\\](https://attacker")
    assert "&lt;img src=x&gt;" in (out / "highlights.cited.md").read_text(encoding="utf-8")
    for name in ("gaps.md", "check.md"):  # the ceiling gap quotes the offer sentence; the requirement term is escaped
        text = (out / name).read_text(encoding="utf-8")
        assert 'production !track."' in text and "&lt;b&gt;dbt&lt;/b&gt; !\\[track\\](https://attacker" in text
    assert result.check.verdict == "PASS" and result.removed == []
