"""Ledger: load with line numbers, lint, draft from CVs, and the index scoring reads."""

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from honestcv import checker
from honestcv.checker import check_document
from honestcv.errors import LedgerError
from honestcv.ledger import build_index, draft_ledger, lint_ledger, load_ledger
from honestcv.models import Ledger, Profile

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "src" / "honestcv" / "samples" / "en"
GOLDEN = REPO / "tests" / "golden"
TODAY = dt.date(2026, 10, 10)
FALLBACK_SKILLS = ["sql", "python", "looker studio", {"name": "excel", "ambiguous": True}, "pandas", "scikit-learn",
                   "google sheets", "zendesk", "customer support", "power bi", "kubernetes", "tableau"]  # fmt: skip


@pytest.fixture(autouse=True)
def skills_vocabulary(monkeypatch):
    """data/skills.yml when it exists, else a short fallback term list."""
    if checker._skills_source() is None:
        monkeypatch.setattr(checker, "_skills_source", lambda: FALLBACK_SKILLS)
    checker.vocab.cache_clear()
    yield
    checker.vocab.cache_clear()


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ledger.yml"
    path.write_text("# Fictional sample. Any resemblance to real people or companies is accidental.\n" + body, encoding="utf-8")
    (tmp_path / "evidence").mkdir(exist_ok=True)
    (tmp_path / "evidence" / "export.txt").write_text("# Fictional sample. Any resemblance to real people or companies is accidental.\n", encoding="utf-8")
    return path


HEAD = """schema_version: 1
person: {name: Maya Lindqvist, as_of: 2026-09-01, stale_after_days: 365}
claims:
"""
GOOD_CLAIM = """  - id: backlog-dashboard
    kind: delivery
    summary: Weekly ticket-backlog dashboard for the support teams
    skills: [sql]
    confirmed:
      built: [{type: file, ref: evidence/export.txt, date: 2024-02-12}]
    beneficiaries: {count: 3, kind: support teams}
    allowed:
      - "Built the weekly ticket-backlog dashboard for three support teams."
    quantities:
      - {name: teams, value: 3, unit: team, state: built}
    review_by: 2027-03-01
"""


def _lint(tmp_path: Path, body: str):
    ledger = load_ledger(_write(tmp_path, HEAD + body))
    return lint_ledger(ledger, tmp_path, TODAY)


def _only(issues, severity: str) -> list[tuple[str, int | None]]:
    return [(i.path, i.line) for i in issues if i.severity == severity]


# ---------------------------------------------------------------- lint: fixtures that must be clean


def test_lint_sample_ledger_is_clean():
    assert lint_ledger(load_ledger(SAMPLES / "ledger.yml"), SAMPLES, TODAY) == []


def test_lint_golden_ledgers():
    assert lint_ledger(load_ledger(GOLDEN / "ledger_pl.yml"), GOLDEN, TODAY) == []
    rules = lint_ledger(load_ledger(GOLDEN / "ledger_rules.yml"), GOLDEN, TODAY)
    assert [(i.severity, i.path) for i in rules] == [("WARN", "claims[2].draft"), ("WARN", "claims[3].review_by")]
    text = (GOLDEN / "ledger_rules.yml").read_text(encoding="utf-8").split("\n")
    assert "draft: true" in text[rules[0].line - 1] and "review_by: 2026-06-01" in text[rules[1].line - 1]
    broken = lint_ledger(load_ledger(GOLDEN / "ledger_broken.yml"), GOLDEN, TODAY)
    assert _only(broken, "ERROR") == [("claims[0].confirmed.shipped", 19), ("claims[1].forbidden_here[0]", 28)]
    assert "needs evidence of type handover_record" in broken[0].message
    assert _only(broken, "WARN") == []


def test_lint_good_claim_is_clean(tmp_path):
    assert _lint(tmp_path, GOOD_CLAIM) == []


# ---------------------------------------------------------------- lint: each ERROR case


def test_lint_duplicate_id(tmp_path):
    issues = _lint(tmp_path, GOOD_CLAIM + GOOD_CLAIM)
    assert ("claims[1].id", 17) in _only(issues, "ERROR")


def test_lint_evidence_of_wrong_type(tmp_path):
    body = GOOD_CLAIM.replace("      built: [{type: file, ref: evidence/export.txt, date: 2024-02-12}]",
                              "      built: [{type: file, ref: evidence/export.txt}]\n      shipped: [{type: file, ref: evidence/export.txt}]")
    issues = _lint(tmp_path, body)
    assert _only(issues, "ERROR") == [("claims[0].confirmed.shipped", 11)]
    assert "state 'shipped' needs evidence of type handover_record" in issues[0].message


def test_lint_missing_path_bad_url_and_free_text(tmp_path):
    body = GOOD_CLAIM.replace("ref: evidence/export.txt", "ref: evidence/missing.txt")
    body = body.replace("    beneficiaries:", "      used: [{type: usage_evidence, ref: \"https://\"}]\n"
                        "      tested: [{type: test_record, ref: my manager saw it}]\n"
                        "      generated: [{type: file, ref: ../outside.txt}]\n    beneficiaries:")
    issues = _lint(tmp_path, body)
    errors = _only(issues, "ERROR")
    assert ("claims[0].confirmed.built[0].ref", 10) in errors
    assert ("claims[0].confirmed.used[0].ref", 11) in errors
    assert ("claims[0].confirmed.tested[0].ref", 12) in errors
    assert ("claims[0].confirmed.generated[0].ref", 13) in errors
    text = {i.path: i.message for i in issues}
    assert "does not exist" in text["claims[0].confirmed.built[0].ref"]
    assert "not a valid URL" in text["claims[0].confirmed.used[0].ref"]
    assert "free text is accepted only for type note" in text["claims[0].confirmed.tested[0].ref"]
    assert "outside the workspace" in text["claims[0].confirmed.generated[0].ref"]


def test_lint_note_accepts_free_text_and_url_is_not_fetched(tmp_path):
    body = GOOD_CLAIM.replace("    beneficiaries:", "      non_cash_value: [{type: note, ref: a free course seat in exchange for feedback}]\n"
                              "    beneficiaries:").replace("ref: evidence/export.txt", 'ref: "https://files.example.invalid/export"')
    body = body.replace("type: file,", "type: url,")
    assert _only(_lint(tmp_path, body), "ERROR") == []


def test_lint_invalid_regexes(tmp_path):
    body = GOOD_CLAIM + "    forbidden_here: ['(?i)broken (']\n    blocks_requirements: ['[a-']\n"
    rules = "rules:\n  forbidden_phrases:\n    - {pattern: '(unclosed', reason: test}\n"
    ledger = load_ledger(_write(tmp_path, HEAD.replace("claims:\n", rules + "claims:\n") + body))
    errors = _only(lint_ledger(ledger, tmp_path, TODAY), "ERROR")
    assert errors == [("rules.forbidden_phrases[0]", 6), ("claims[0].forbidden_here[0]", 20), ("claims[0].blocks_requirements[0]", 21)]


def test_lint_ceiling_needs_statement_and_its_own_patterns(tmp_path):
    body = GOOD_CLAIM + """  - id: no-team-lead
    kind: ceiling
    forbidden_here: ['(?i)\\bI led (a|the) team\\b']
    allowed:
      - "I have not led a team yet."
      - "I led the team of analysts."
    review_by: 2027-03-01
"""
    issues = _lint(tmp_path, body)
    errors = _only(issues, "ERROR")
    assert ("claims[1].statement", 17) in errors
    assert ("claims[1].allowed[1]", 22) in errors
    assert ("claims[1].allowed[0]", 21) not in errors  # a negation and the scope words of a ceiling sentence are fine
    assert len(errors) == 2


def test_lint_claim_id_with_private_name(tmp_path):
    body = GOOD_CLAIM.replace("id: backlog-dashboard", "id: brennvik-backlog").replace(
        "    review_by:", "    private_names: [Brennvik Parcel]\n    review_by:")
    assert ("claims[0].id", 5) in _only(_lint(tmp_path, body), "ERROR")


def test_lint_allowed_sentence_blocked_by_its_own_guards(tmp_path):
    body = GOOD_CLAIM.replace('"Built the weekly ticket-backlog dashboard for three support teams."',
                              '"Built the weekly ticket-backlog dashboard for five support teams across the company."')
    issues = _lint(tmp_path, body)
    assert ("claims[0].allowed[0]", 13) in _only(issues, "ERROR")
    joined = " ".join(i.message for i in issues)
    assert "QUANTITY_MISMATCH" in joined and "SCOPE_INFLATION" in joined


def test_lint_missing_qualifier_is_an_error_and_authorship_review_a_warn(tmp_path):
    body = GOOD_CLAIM.replace("    review_by:", "    required_qualifiers: [course project]\n    authorship: {role: led}\n    review_by:")
    issues = _lint(tmp_path, body)
    assert sorted((i.severity, i.path, i.message.split(": ")[1]) for i in issues) == [
        ("ERROR", "claims[0].allowed[0]", "MISSING_QUALIFIER"), ("WARN", "claims[0].allowed[0]", "AUTHORSHIP")]


def test_lint_uses_quantities_and_facts_only_never_the_allowed_sentences(tmp_path):
    body = GOOD_CLAIM.replace("      - {name: teams, value: 3, unit: team, state: built}\n", "")
    body = body.replace("    quantities:\n", "    quantities: []\n")
    assert any("QUANTITY_MISMATCH" in i.message for i in _lint(tmp_path, body))


def test_lint_schema_violation_names_the_field_and_line_never_the_value(tmp_path):
    path = _write(tmp_path, HEAD + GOOD_CLAIM.replace("kind: delivery", "kind: secret-kind-value"))
    with pytest.raises(LedgerError) as err:
        load_ledger(path)
    assert err.value.line == 6 and "claims[0].kind" in err.value.message
    assert "secret-kind-value" not in err.value.message and "secret-kind-value" not in (err.value.hint or "")


def test_load_ledger_yaml_error_and_empty(tmp_path):
    path = _write(tmp_path, "schema_version: 1\nclaims: [\n")
    with pytest.raises(LedgerError) as err:
        load_ledger(path)
    assert err.value.line is not None
    assert load_ledger(_write(tmp_path, "schema_version: 1\nperson: {stale_after_days: 365}\nclaims: []\n")) is None
    assert load_ledger(tmp_path / "missing.yml") is None


# ---------------------------------------------------------------- lint: each WARN case


def test_lint_warnings(tmp_path):
    head = HEAD.replace("as_of: 2026-09-01", "as_of: 2025-01-01")
    body = GOOD_CLAIM.replace("review_by: 2027-03-01", "review_by: 2026-06-01") + """  - id: churn-capstone
    kind: artifact
    summary: Churn prediction model
    confirmed:
      built: [{type: file, ref: evidence/export.txt}]
  - id: photo-line
    kind: delivery
    summary: Answered customer questions by e-mail.
    allowed: ["Answered customer questions by e-mail."]
    draft: true
  - id: private-output
    kind: output
    summary: Descriptions generated by a template tool
    visibility: never
"""
    issues = lint_ledger(load_ledger(_write(tmp_path, head + body)), tmp_path, TODAY)
    assert _only(issues, "ERROR") == []
    assert _only(issues, "WARN") == [("person.as_of", 3), ("claims[0].review_by", 16), ("claims[1].allowed", 17), ("claims[2].draft", 26)]


def test_lint_does_not_run_the_stale_guard(tmp_path):
    body = GOOD_CLAIM.replace("review_by: 2027-03-01", "review_by: 2026-01-01")
    assert [i.message for i in _lint(tmp_path, body)] == ["backlog-dashboard: review_by 2026-01-01 has passed"]


def test_lint_without_line_numbers_for_a_ledger_built_in_code():
    ledger = Ledger.model_validate(yaml.safe_load(HEAD + GOOD_CLAIM + GOOD_CLAIM))
    issues = lint_ledger(ledger, SAMPLES, TODAY)
    assert ("claims[1].id", None) in [(i.path, i.line) for i in issues if i.severity == "ERROR"]
    assert all(i.line is None for i in issues)


# ---------------------------------------------------------------- index


def test_build_index_matches_the_frozen_expectation():
    expect = yaml.safe_load((GOLDEN / "scoring_expect.yml").read_text(encoding="utf-8"))["ledger_index"]
    index = build_index(load_ledger(SAMPLES / "ledger.yml"))
    assert index.skill_backing == expect["skill_backing"]
    assert index.draft_skills == expect["draft_skills"]
    assert [list(p) for p in index.ceiling_patterns] == expect["ceiling_patterns"]
    assert build_index(None) is None
    rules = build_index(load_ledger(GOLDEN / "ledger_rules.yml"))
    assert rules.skill_backing == {} and rules.draft_skills == {}


# ---------------------------------------------------------------- draft


def _sample_profiles() -> tuple[list[Profile], dict[str, str]]:
    experience = [("Support Operations Analyst", "2020-2026"), ("Customer Support Agent", "2016-2020")]
    texts, profiles = {}, []
    for name in ("maya-analytics.md", "maya-support-ops.md"):
        texts[name] = (SAMPLES / "cvs" / name).read_text(encoding="utf-8")
        profiles.append(Profile(cv_id=name, cv_sha256="0" * 64, skills=["sql"], titles=[t for t, d in experience],
                                target_titles=[], years=6, level=None, languages={"en": "C1"}, experience=experience))  # fmt: skip
    return profiles, texts


def test_ledger_draft(tmp_path):
    profiles, texts = _sample_profiles()
    text = draft_ledger(profiles, texts, today=TODAY)
    assert text.startswith("# honestcv claims ledger, drafted from your CVs on 2026-10-10.")
    path = tmp_path / "ledger.yml"
    path.write_text(text, encoding="utf-8")
    ledger = load_ledger(path)
    issues = lint_ledger(ledger, tmp_path, TODAY)
    assert {i.severity for i in issues} == {"WARN"}
    assert [i.path for i in issues] == [f"claims[{n}].draft" for n in range(len(ledger.claims))]
    assert all(c.draft and c.allowed == [c.summary] and c.confirmed == {} for c in ledger.claims)
    summaries = [c.summary for c in ledger.claims]
    # 2 job lines + 6 bullets from the first CV, 2 more bullets from the second; then the other units the check
    # reads as claims: profile sentence, Skills line, languages line and course heading of the first CV,
    # headline, Skills line and profile sentence of the second
    assert len(summaries) == len(set(summaries)) == 17
    assert len({c.id for c in ledger.claims}) == len(ledger.claims)
    by_summary = {c.summary: c for c in ledger.claims}
    dashboard = by_summary["Built the weekly ticket-backlog dashboard that three support teams use in their Monday reviews."]
    assert dashboard.kind == "delivery" and dashboard.draft_source == "cvs/maya-analytics.md#line 22"
    assert by_summary["Wrote the SQL queries behind the monthly ticket volume report."].skills == ["sql"]
    bio = by_summary["Support Operations Analyst, Brennvik Parcel, 2020-2026"]
    assert bio.kind == "biography" and bio.facts == {"start_year": 2020, "end_year": 2026, "precision": "year"}
    assert "power bi" in by_summary["Built the monthly Power BI report on ticket volume for the support managers."].skills
    assert by_summary["Trained new agents on the help-desk tools."].draft_source == "cvs/maya-support-ops.md#line 29"


def test_drafted_ledger_accepts_cv_lines_word_for_word_only(tmp_path):
    profiles, texts = _sample_profiles()
    path = tmp_path / "ledger.yml"
    path.write_text(draft_ledger(profiles, texts, today=TODAY), encoding="utf-8")
    ledger = load_ledger(path)
    doc = tmp_path / "letter.md"
    doc.write_text("Wrote the SQL queries behind the monthly ticket volume report.\n\n"
                   "Wrote the SQL queries behind every ticket volume report.\n", encoding="utf-8")  # fmt: skip
    result = check_document(doc, ledger, ws_root=tmp_path, today=TODAY)
    assert [(f.line, f.rule) for f in result.flags if f.line == 1] == [(1, "DRAFT_CLAIM")]
    assert ("DRAFT_REWORDED", "BLOCK") in {(f.rule, f.severity) for f in result.flags if f.line == 3}


def test_draft_with_no_cv_lines_is_an_empty_ledger(tmp_path):
    text = draft_ledger([], {"notes.md": "Just a paragraph.\n"}, today=TODAY)
    path = tmp_path / "ledger.yml"
    path.write_text(text, encoding="utf-8")
    assert "claims: []" in text and load_ledger(path) is None


@pytest.mark.parametrize("name", ["maya-analytics.md", "maya-support-ops.md"])
def test_the_unchanged_cv_checks_clean_against_its_own_draft(tmp_path, name):
    profiles, texts = _sample_profiles()
    path = tmp_path / "ledger.yml"
    path.write_text(draft_ledger([p for p in profiles if p.cv_id == name], {name: texts[name]}, today=TODAY), encoding="utf-8")
    ledger = load_ledger(path)
    skills = [c for c in ledger.claims if c.kind == "skill"]
    assert len(skills) == 1 and skills[0].summary.startswith("Skills: ") and skills[0].draft
    assert "excel" in skills[0].skills and "google sheets" in skills[0].skills  # every skills.yml term of the line
    result = check_document(SAMPLES / "cvs" / name, ledger, ws_root=tmp_path, today=TODAY)
    assert result.verdict == "REVIEW" and {(f.rule, f.severity) for f in result.flags} == {("DRAFT_CLAIM", "REVIEW")}
    assert all("confirm it in ledger.yml" in f.message for f in result.flags)
    skills_line = next(n for n, line in enumerate(texts[name].split("\n"), 1) if line.startswith("Skills: "))
    assert [f.claim_id for f in result.flags if f.line == skills_line] == [skills[0].id]


def test_draft_follows_the_units_the_check_reads(tmp_path):
    cv = ("## Experience\n\n### Analyst, Example Freight, 2021-2024\n\n- Built the weekly finance report pack and\n"
          "  wrote its SQL checks.\n- Trained two new agents.\n\nCertified Scrum Master, 2023.\n\n"
          "### BSc Economics, Example University, 2017-2020\n")  # fmt: skip
    profile = Profile(cv_id="cv.md", cv_sha256="0" * 64, skills=[], titles=["Analyst"], target_titles=[], years=3, level=None,
                      languages={}, experience=[("Analyst", "2021-2024")])  # fmt: skip
    path = tmp_path / "ledger.yml"
    path.write_text(draft_ledger([profile], {"cv.md": cv}, today=TODAY), encoding="utf-8")
    ledger = load_ledger(path)
    by_summary = {c.summary: c for c in ledger.claims}
    assert "Built the weekly finance report pack and wrote its SQL checks." in by_summary  # a wrapped bullet is one unit
    assert by_summary["BSc Economics, Example University, 2017-2020"].facts == {"start_year": 2017, "end_year": 2020, "precision": "year"}
    assert "Certified Scrum Master, 2023." in by_summary and "Experience" not in by_summary
    doc = tmp_path / "cv.md"
    doc.write_text(cv, encoding="utf-8")
    result = check_document(doc, ledger, ws_root=tmp_path, today=TODAY)
    assert {(f.rule, f.severity) for f in result.flags} == {("DRAFT_CLAIM", "REVIEW")}


def test_cli_init_draft_and_check_of_the_same_cv(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "HONESTCV"))} | {"HOME": str(home), "USERPROFILE": str(home)}

    def run(*args: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "honestcv", *args, "--today", "2026-10-11"]
        return subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180, check=False)

    assert run("init", "--cv", str(SAMPLES / "cvs" / "maya-analytics.md")).returncode == 0
    assert run("ledger", "draft").returncode == 0
    checked = run("check", str(home / "honestcv" / "cvs" / "maya-analytics.md"))
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert checked.stdout.startswith("REVIEW · 0 lines blocked · 12 lines to review · 0 lines passed")
