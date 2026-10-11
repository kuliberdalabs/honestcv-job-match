"""Golden acceptance for the claims checker: tests/golden/checker_bad.yml, checker_good.yml and the planted letter."""

from pathlib import Path

import pytest
import yaml

from honestcv import checker
from honestcv.checker import check_document, render_check, summary_line
from honestcv.ledger import load_ledger

REPO = Path(__file__).resolve().parents[2]
BAD = yaml.safe_load((REPO / "tests" / "golden" / "checker_bad.yml").read_text(encoding="utf-8"))
GOOD = yaml.safe_load((REPO / "tests" / "golden" / "checker_good.yml").read_text(encoding="utf-8"))
TODAY = BAD["today"]

# If data/skills.yml is missing, the checker tests fall back to a short term list.
FALLBACK_SKILLS = [
    "sql", "python", "looker studio", {"name": "excel", "ambiguous": True}, "airflow", "dbt", "pandas", "tableau",
    "scikit-learn", {"name": "google sheets", "aliases": ["gsheets"]}, "zendesk", "customer support", "kubernetes",
    "power bi", {"name": "sheets", "ambiguous": True}, {"name": "power", "ambiguous": True},
]  # fmt: skip


@pytest.fixture(autouse=True)
def skills_vocabulary(monkeypatch):
    if checker._skills_source() is None:
        monkeypatch.setattr(checker, "_skills_source", lambda: FALLBACK_SKILLS)
    checker.vocab.cache_clear()
    yield
    checker.vocab.cache_clear()


def _ledger(rel: str):
    return load_ledger(REPO / rel)


def _check_line(tmp_path: Path, ledger_rel: str, text: str):
    doc = tmp_path / "line.md"
    doc.write_text(text + "\n", encoding="utf-8")
    ws_root = REPO / BAD["ws_root"][ledger_rel]
    return check_document(doc, _ledger(ledger_rel), ws_root=ws_root, today=TODAY)


def _codes(result, line: int | None = None) -> set[tuple[str, str]]:
    return {(f.rule, f.severity) for f in result.flags if line is None or f.line == line}


@pytest.mark.parametrize("case", BAD["lines"], ids=[c["id"] for c in BAD["lines"]])
def test_golden_bad(tmp_path, case):
    result = _check_line(tmp_path, case["ledger"], case["text"])
    expected = {(e["rule"], e["severity"]) for e in case["expect"]}
    got = _codes(result)
    if case.get("exact"):
        assert {r for r, s in got} == {r for r, s in expected}, render_check(result)
    assert expected <= got, render_check(result)
    if "verdict" in case:
        assert result.verdict == case["verdict"], render_check(result)
    if "lint_errors" in case:
        assert sum(1 for f in result.flags if f.rule == "LEDGER_LINT") == case["lint_errors"]


@pytest.mark.parametrize("case", GOOD["lines"], ids=[c["text"][:40] for c in GOOD["lines"]])
def test_golden_good(tmp_path, case):
    result = _check_line(tmp_path, case["ledger"], case["text"])
    assert result.verdict == "PASS" and result.flags == [], render_check(result)


def test_planted_letter():
    spec = BAD["planted_letter"]
    result = check_document(REPO / spec["document"], _ledger(spec["ledger"]), ws_root=REPO / BAD["ws_root"][spec["ledger"]], today=TODAY)
    report = render_check(result)
    assert result.verdict == spec["verdict"]
    assert result.units_total == spec["units_total"] and result.units_claim_bearing == spec["units_claim_bearing"]
    assert summary_line(result) == spec["summary"] and report.startswith(spec["summary"] + "\n")
    for line, case in spec["lines"].items():
        flags = [f for f in result.flags if f.line == line]
        assert {(f.rule, f.severity, f.claim_id) for f in flags} == {(e["rule"], e["severity"], e["claim_id"]) for e in case["expect"]}, report
        assert all(f.quote == case["text"] for f in flags)
    assert not [f for f in result.flags if f.line in spec["not_claim_bearing"]]
    assert {f.line for f in result.flags} == {3, 5, 7, 9}


def test_planted_letter_messages_name_ledger_fields():
    spec = BAD["planted_letter"]
    result = check_document(REPO / spec["document"], _ledger(spec["ledger"]), ws_root=REPO / BAD["ws_root"][spec["ledger"]], today=TODAY)
    by_rule = {f.rule: f for f in result.flags if f.line == 3}
    assert by_rule["CEILING_BREACH"].field == "claims[3].forbidden_here[0]"
    assert 'matches "deployed our churn model to production"' in by_rule["CEILING_BREACH"].message
    assert by_rule["NOT_CONFIRMED"].field == "claims[2].not_confirmed[2]"
    assert by_rule["UNBACKED_OUTCOME"].field == "claims[2].outcomes_allowed"
    assert by_rule["MISSING_QUALIFIER"].field == "claims[2].required_qualifiers[0]"
    assert by_rule["STATE_OVERSTATED"].message == '"deployed" implies shipped; highest confirmed state of churn-capstone is tested'
    assert by_rule["STATE_OVERSTATED"].safe_rewrite == "Built a churn prediction model in Python with pandas and scikit-learn as a course capstone."
    line9 = [f for f in result.flags if f.line == 9 and f.rule == "UNBACKED_OUTCOME"][0]
    assert 'not_confirmed[0] "shorter handling time"' in line9.message
