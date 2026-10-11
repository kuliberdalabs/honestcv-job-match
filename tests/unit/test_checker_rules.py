"""Claims checker rules: exact and near-exact paths, claim-bearing units, numbers and clauses,
the authorship matrix, drafts, markers, gaps, and that a check never writes."""

import datetime as dt
import shutil
from pathlib import Path

import pytest
import yaml

from honestcv import checker
from honestcv.checker import check_document, render_check, strip_markers
from honestcv.ledger import load_ledger
from honestcv.models import Ledger, Offer, Requirement

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "src" / "honestcv" / "samples" / "en"
TODAY = dt.date(2026, 10, 10)
FALLBACK_SKILLS = ["sql", "python", "looker studio", {"name": "excel", "ambiguous": True}, "pandas", "scikit-learn",
                   "google sheets", "zendesk", "customer support", "power bi", "kubernetes", "tableau", "airflow", "dbt"]  # fmt: skip


@pytest.fixture(autouse=True)
def skills_vocabulary(monkeypatch):
    """data/skills.yml when it exists, else a short fallback term list."""
    if checker._skills_source() is None:
        monkeypatch.setattr(checker, "_skills_source", lambda: FALLBACK_SKILLS)
    checker.vocab.cache_clear()
    yield
    checker.vocab.cache_clear()


@pytest.fixture
def maya():
    return load_ledger(SAMPLES / "ledger.yml")


def _check(tmp_path: Path, ledger: Ledger, text: str, **kw):
    doc = tmp_path / "doc.md"
    doc.write_text(text + "\n", encoding="utf-8")
    return check_document(doc, ledger, ws_root=kw.pop("ws_root", SAMPLES), today=TODAY, **kw)


def _rules(result, line: int = 1) -> set[str]:
    return {f.rule for f in result.flags if f.line == line}


def _path(ledger: Ledger, text: str) -> tuple[str, str | None]:
    ctx = checker._Ctx.build(ledger, TODAY)
    path, claim, _ = checker._attribute(checker._unit(1, text, ctx), ctx, None)
    return path, claim.id if claim else None


def _ledger(claims: list[dict], **person) -> Ledger:
    return Ledger.model_validate({"schema_version": 1, "person": {"as_of": "2026-09-01", "stale_after_days": 365, **person}, "claims": claims})


URL = [{"type": "url", "ref": "https://files.example.invalid/x"}]
HANDOVER = [{"type": "handover_record", "ref": "https://files.example.invalid/handover"}]


# ---------------------------------------------------------------- exact and near-exact


def test_exact_vs_near_exact(tmp_path, maya):
    allowed = "Built the weekly ticket-backlog dashboard that three support teams use in their Monday reviews."
    assert _path(maya, allowed) == ("exact", "backlog-dashboard")
    assert _path(maya, allowed.lower().rstrip(".")) == ("exact", "backlog-dashboard")
    wide = "Built the weekly ticket-backlog dashboard that three support teams use in their company-wide Monday reviews."
    assert _path(maya, wide) == ("near", "backlog-dashboard")
    assert {"SCOPE_INFLATION", "NOT_CONFIRMED"} <= _rules(_check(tmp_path, maya, wide))
    plural = allowed.replace("dashboard", "dashboards")
    assert _path(maya, plural) == ("near", "backlog-dashboard")
    drafted = maya.model_copy(deep=True)
    drafted.claims[0].draft = True
    assert _rules(_check(tmp_path, drafted, allowed)) == {"DRAFT_CLAIM"}
    assert "DRAFT_REWORDED" in _rules(_check(tmp_path, drafted, plural))


def test_exact_path_runs_only_numbers_qualifiers_and_drafts(tmp_path):
    line = "Deployed 5 weekly reports to production for Example Freight in 2019."
    claim = {"id": "report-line", "kind": "delivery", "summary": line, "allowed": [line], "draft": True}
    # Outside the exact path this line would get STATE_OVERSTATED, SCOPE_INFLATION, UNKNOWN_ENTITY and more.
    assert _rules(_check(tmp_path, _ledger([claim]), line)) == {"DRAFT_CLAIM"}
    claim["quantities"] = [{"name": "reports", "value": 5, "unit": "report", "state": "built"}]
    assert _rules(_check(tmp_path, _ledger([claim]), line)) == {"DRAFT_CLAIM", "QUANTITY_MISMATCH"}  # clause implies shipped
    assert "STATE_OVERSTATED" in _rules(_check(tmp_path, _ledger([claim]), line.replace("5 weekly", "5 monthly")))


# ---------------------------------------------------------------- claim-bearing units


@pytest.mark.parametrize("text", ["Certified Scrum trainer.", "BSc in economics.", "Fluent in German.", "Native Swedish speaker.",
                                  "Senior analyst.", "Magister ekonomii.", "Biegła znajomość angielskiego.", "English at C1 level."])  # fmt: skip
def test_claim_bearing_words(maya, text):
    ctx = checker._Ctx.build(maya, TODAY)
    assert checker._claim_bearing(checker._unit(1, text, ctx), ctx)


@pytest.mark.parametrize("text", ["Kind regards,", "References available on request.", "Z poważaniem,", "Dear hiring team,",
                                  "Wyrażam zgodę na przetwarzanie moich danych osobowych w celu rekrutacji.", "Maya Lindqvist",
                                  "I am excited to apply for this role at Tallgrove Analytics."])  # fmt: skip
def test_not_claim_bearing(maya, text):
    ctx = checker._Ctx.build(maya, TODAY)
    assert not checker._claim_bearing(checker._unit(1, text, ctx), ctx)


def test_non_claim_lines_still_get_document_wide_guards(tmp_path, maya):
    result = _check(tmp_path, maya, "Dear senior hiring manager,")
    assert result.units_claim_bearing == 0 and _rules(result) == {"FORBIDDEN_PHRASE"}


# ---------------------------------------------------------------- numbers and clauses


def _reports(**quantity) -> Ledger:
    return _ledger([{"id": "report-pack", "kind": "delivery", "summary": "Weekly finance report pack",
                     "confirmed": {"built": URL, "shipped": HANDOVER, "used": [{"type": "usage_evidence", "ref": "https://files.example.invalid/u"}]},
                     "quantities": [{"name": "reports", "value": 5, "unit": "report", "state": "built", **quantity}],
                     "allowed": ["Built a weekly finance report pack of 5 reports."]}])  # fmt: skip


def test_numbers_do_not_count_toward_support(maya):
    ctx = checker._Ctx.build(maya, TODAY)
    weights = checker._weights(checker._unit(1, "Built 3 dashboards for three support teams in 2024 over 2 years.", ctx), ctx)
    assert set(weights) == {"dashboard", "support", "team"}


def test_a_numbers_state_comes_from_its_own_clause(tmp_path):
    assert "QUANTITY_MISMATCH" not in _rules(_check(tmp_path, _reports(), "Built 5 weekly finance reports; the finance team used them."))
    assert "QUANTITY_MISMATCH" in _rules(_check(tmp_path, _reports(), "The finance team used 5 weekly finance reports."))


def test_venue_verbs_imply_no_state(tmp_path):
    no_venue = _check(tmp_path, _reports(), "Published 5 weekly finance reports.")
    assert _rules(no_venue) == {"QUANTITY_MISMATCH"}
    assert "venue" in no_venue.flags[0].message
    assert _rules(_check(tmp_path, _reports(venue="intranet"), "Published 5 weekly finance reports.")) == set()


def test_vague_quantities_fail(tmp_path):
    assert "QUANTITY_MISMATCH" in _rules(_check(tmp_path, _reports(), "Built several weekly finance reports."))
    assert "QUANTITY_MISMATCH" in _rules(_check(tmp_path, _reports(), "Zbudowałam kilkanaście raportów finance report pack."))


def test_plus_and_over_need_a_strictly_larger_value(tmp_path):
    assert "QUANTITY_MISMATCH" in _rules(_check(tmp_path, _reports(), "Built 5+ weekly finance reports."))
    assert "QUANTITY_MISMATCH" not in _rules(_check(tmp_path, _reports(), "Built over 4 weekly finance reports."))


def test_years_and_durations_go_to_the_duration_guard_only(tmp_path, maya):
    rules = _rules(_check(tmp_path, maya, "Built the weekly ticket-backlog dashboard for three support teams in 2019."))
    assert "DURATION_MISMATCH" in rules and "QUANTITY_MISMATCH" not in rules  # 2019 is before the evidence dates (2024)
    rules = _rules(_check(tmp_path, maya, "Customer support and support operations at a parcel company for 9 years."))
    assert rules == set()
    rules = _rules(_check(tmp_path, maya, "Customer support and support operations at a parcel company since 2014."))
    assert rules == {"DURATION_MISMATCH"}


# ---------------------------------------------------------------- authorship


UNITS = {
    "authored": "Wrote the supplier feed integration for the catalogue.",
    "led": "Led the supplier feed integration for the catalogue.",
    "contributed": "Contributed to the supplier feed integration for the catalogue.",
    "directed": "Specified the supplier feed integration for the catalogue.",
}
MATRIX = {
    "sole": {"authored": None, "led": None, "contributed": None, "directed": None},
    "led": {"authored": "REVIEW", "led": None, "contributed": None, "directed": None},
    "contributed": {"authored": "BLOCK", "led": "BLOCK", "contributed": None, "directed": "REVIEW"},
    "directed": {"authored": "BLOCK", "led": "REVIEW", "contributed": "REVIEW", "directed": None},
}


def _feed(role: str, **authorship) -> Ledger:
    return _ledger([{"id": "feed-integration", "kind": "delivery", "summary": "Supplier feed integration for the catalogue",
                     "confirmed": {"built": URL, "shipped": HANDOVER}, "authorship": {"role": role, **authorship},
                     "allowed": ["Delivered the supplier feed integration for the catalogue."]}])  # fmt: skip


@pytest.mark.parametrize("role", MATRIX)
@pytest.mark.parametrize("cls", UNITS)
def test_authorship_matrix(tmp_path, role, cls):
    result = _check(tmp_path, _feed(role), UNITS[cls])
    got = [f.severity for f in result.flags if f.rule == "AUTHORSHIP"]
    assert got == ([MATRIX[role][cls]] if MATRIX[role][cls] else []), render_check(result)


def test_authorship_nouns(tmp_path):
    assert [f.severity for f in _check(tmp_path, _feed("directed"), "Author of the supplier feed integration for the catalogue.").flags
            if f.rule == "AUTHORSHIP"] == ["BLOCK"]  # fmt: skip
    polish = _ledger([{"id": "integracja", "kind": "delivery", "summary": "Integracja dostawcy dla katalogu",
                       "confirmed": {"built": URL, "shipped": HANDOVER}, "authorship": {"role": "contributed"},
                       "allowed": ["Współtworzyłam integrację dostawcy dla katalogu."]}])  # fmt: skip
    assert "AUTHORSHIP" in _rules(_check(tmp_path, polish, "Autor integracji dostawcy dla katalogu."))
    assert "AUTHORSHIP" in _rules(_check(tmp_path, polish, "Napisałam integrację dostawcy dla katalogu."))


def test_agent_assisted_work_you_cannot_defend(tmp_path):
    shaky = _feed("sole", agent_assisted=True, can_defend_end_to_end=False)
    assert [f.severity for f in _check(tmp_path, shaky, UNITS["authored"]).flags if f.rule == "AUTHORSHIP"] == ["BLOCK"]
    assert "AUTHORSHIP" not in _rules(_check(tmp_path, shaky, UNITS["led"]))
    sure = _feed("sole", agent_assisted=True, can_defend_end_to_end=True)
    assert "AUTHORSHIP" not in _rules(_check(tmp_path, sure, UNITS["authored"]))


# ---------------------------------------------------------------- drafts, markers, gaps


def test_draft_claims(tmp_path):
    ledger = load_ledger(REPO / "tests" / "golden" / "ledger_rules.yml")
    ws = REPO / "tests" / "golden"
    verbatim = _check(tmp_path, ledger, "Planned the photo-shoot schedule for new catalogue items.", ws_root=ws)
    assert [(f.rule, f.severity) for f in verbatim.flags] == [("DRAFT_CLAIM", "REVIEW")] and verbatim.verdict == "REVIEW"
    reworded = _check(tmp_path, ledger, "Planned the photo-shoot schedule for the new catalogue items.", ws_root=ws)
    assert ("DRAFT_REWORDED", "BLOCK") in {(f.rule, f.severity) for f in reworded.flags} and reworded.verdict == "BLOCK"


def test_strip_markers():
    text = "# Highlights\n\n- Built X. <!-- c:backlog-dashboard -->\n- Led Y.<!-- c:a,b -->\n<!-- keep me -->\n"
    assert strip_markers(text) == "# Highlights\n\n- Built X.\n- Led Y.\n<!-- keep me -->\n"


def test_citation_markers(tmp_path, maya):
    cited = _check(tmp_path, maya, "- Built a churn prediction model in Python with pandas and scikit-learn as a course capstone. <!-- c:churn-capstone -->")
    assert cited.verdict == "PASS" and cited.units_backed == 1
    wrong = _check(tmp_path, maya, "- Led the help-desk tag cleanup that cut the tag list from 41 to 18. <!-- c:churn-capstone -->")
    assert _rules(wrong) == {"CITATION_MISMATCH"}
    unmarked = _check(tmp_path, maya, "# Highlights\n\n- Kind regards", require_markers=True)
    assert _rules(unmarked, 3) == {"UNBACKED"}


def test_gaps_from_requirements_and_ceilings(tmp_path, maya):
    offer = Offer(id="0123456789", source="import:json", title="Machine Learning Engineer", company="Vossgrind Labs",
                  attribution="your file", skills=["python", "kubernetes"],
                  description="Requirements\n- Experience deploying machine learning models to production.\n- Python.")  # fmt: skip
    reqs = [Requirement(term="python", must=True, in_profile=True), Requirement(term="excel", must=True, in_profile=True),
            Requirement(term="tableau", must=False, in_profile=False)]  # fmt: skip
    result = _check(tmp_path, maya, "Kind regards,", offer=offer, requirements=reqs)
    assert result.gaps == ['ml-production-ceiling: "Experience deploying machine learning models to production."', "excel"]
    assert _check(tmp_path, maya, "Kind regards,", offer=offer).gaps[1:] == ["kubernetes"]
    assert result.verdict == "PASS" and "not added anywhere" in render_check(result)


def test_ledger_lint_errors_block_the_check(tmp_path):
    broken = load_ledger(REPO / "tests" / "golden" / "ledger_broken.yml")
    result = _check(tmp_path, broken, "Built the weekly sales report for the catalogue team.", ws_root=REPO / "tests" / "golden")
    assert [f.rule for f in result.flags] == ["LEDGER_LINT", "LEDGER_LINT"] and result.verdict == "BLOCK"
    assert render_check(result).startswith("BLOCK · the ledger has 2 errors, so no line was checked")


def test_verbs_extra_from_the_ledger(tmp_path):
    ledger = _ledger([{"id": "report-pack", "kind": "delivery", "summary": "Weekly finance report pack",
                       "confirmed": {"built": URL}, "allowed": ["Built the weekly finance report pack."]}])  # fmt: skip
    ledger.rules.verbs_extra = {"shipped": ["went live with"]}
    assert "STATE_OVERSTATED" in _rules(_check(tmp_path, ledger, "Went live with the weekly finance report pack."))


def test_non_cash_value_described_as_pay(tmp_path):
    ledger = _ledger([{"id": "course-seat", "kind": "delivery", "summary": "Course feedback sessions for a course seat",
                       "confirmed": {"non_cash_value": [{"type": "note", "ref": "a free course seat in exchange for feedback"}]},
                       "allowed": ["Gave feedback sessions in exchange for a course seat."]}])  # fmt: skip
    assert "VISIBILITY" in _rules(_check(tmp_path, ledger, "Paid course feedback sessions for a course seat."))


# ---------------------------------------------------------------- report only


def test_check_read_only(tmp_path, maya):
    folder = tmp_path / "letters"
    folder.mkdir()
    doc = folder / "cover-letter-planted.md"
    shutil.copy(SAMPLES / "letters" / "cover-letter-planted.md", doc)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    result = check_document(doc, maya, ws_root=SAMPLES, today=TODAY)
    assert result.verdict == "BLOCK"
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert result.document_sha256 == __import__("hashlib").sha256(before[doc.name]).hexdigest()


def test_pdf_and_docx_are_read_through_the_cv_reader(tmp_path, maya, monkeypatch):
    from honestcv import profile
    from honestcv.models import CvText

    doc = tmp_path / "letter.docx"
    doc.write_bytes(b"PK fake")
    monkeypatch.setattr(profile, "load_cv_text", lambda path: CvText(text="Expert in Kubernetes.\n"))
    assert _rules(check_document(doc, maya, ws_root=SAMPLES, today=TODAY)) == {"UNBACKED"}


def test_json_dump_uses_the_schema_key(tmp_path, maya):
    data = _check(tmp_path, maya, "Expert in Kubernetes.").model_dump(mode="json", by_alias=True)
    assert data["schema"] == "honestcv.check.v1" and data["verdict"] == "BLOCK"
    assert yaml.safe_load(yaml.safe_dump(data))["flags"][0]["rule"] == "UNBACKED"
