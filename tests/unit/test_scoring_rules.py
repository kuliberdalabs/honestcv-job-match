"""Gates, penalties, the surface rule and ranking."""

import datetime as dt
import time

from honestcv.config import Config
from honestcv.models import LedgerIndex, MatchResult, Offer, Preferences, Profile
from honestcv.scoring import MAX_TEXT, best_per_offer, check_gates, extract_requirements, monthly_salary, score_all, score_offer

TODAY = dt.date(2026, 10, 10)
REQ = "Requirements\n"
THREE = "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas\n"


def offer(title="Data Analyst", description="", id="o1", **kw) -> Offer:
    kw.setdefault("url", "https://jobs.example.invalid/1")
    return Offer(id=id, source="test", title=title, description=description, attribution="test", **kw)


def profile(**kw) -> Profile:
    base = dict(cv_id="cv.md", cv_sha256="0" * 64, skills=[], titles=[], target_titles=[], years=None, level=None, languages={})
    return Profile(**{**base, **kw})


def config(**kw) -> Config:
    return Config.model_validate(kw)


CEILING = LedgerIndex(
    skill_backing={}, draft_skills={},
    ceiling_patterns=[("ml-production-ceiling", r"(?i)models? (to|in|into) production"),
                      ("ml-production-ceiling", r"(?i)(experience|track record) (deploying|in deploying|with deploying) (ml|machine[- ]learning) models")],
)  # fmt: skip


def gate(o, index=None, **prefs):
    return check_gates(o, Preferences(**prefs), index, TODAY)


# ---------------------------------------------------------------- gates


def test_gates():
    g = gate(offer("Sales Representative"), deny_titles=["sales*"])
    assert (g.code, g.quote) == ("deny_title", "Sales Representative") and "sales*" in g.detail
    g = gate(offer(description="We offer a relocation package."), deny_terms=["relocation"])
    assert g.code == "deny_term" and "relocation" in g.quote and "relocation" in g.detail

    g = gate(offer(description="Pay: this role is commission-based only. No base salary."))
    assert g.code == "commission_only" and "commission-based only" in g.quote and g.detail
    g = gate(offer(description="Wynagrodzenie wyłącznie prowizyjne."))
    assert g.code == "commission_only"  # Polish phrase
    assert gate(offer(description="This is not a commission-only role. Base salary plus bonus.")) is None  # negated
    assert gate(offer(description="Pure commission.")).code == "commission_only"

    g = gate(offer(description="Candidates complete an unpaid test task."))
    assert g.code == "unpaid_task" and "unpaid test task" in g.quote
    assert gate(offer(description="Bezpłatne zadanie rekrutacyjne na koniec procesu.")).code == "unpaid_task"
    assert gate(offer(description="Compensation is equity only.")).code == "equity_only"
    assert gate(offer(description="Wynagrodzenie wyłącznie w udziałach.")).code == "equity_only"

    g = gate(offer(valid_through=TODAY - dt.timedelta(days=1)))
    assert g.code == "expired" and "2026-10-09" in g.detail
    assert gate(offer(valid_through=TODAY)) is None  # open through today

    g = gate(offer(posted_at=TODAY - dt.timedelta(days=31)), freshness_days=30)
    assert g.code == "stale" and "31 days" in g.detail
    assert gate(offer(posted_at=TODAY - dt.timedelta(days=30)), freshness_days=30) is None
    assert gate(offer(posted_at=TODAY - dt.timedelta(days=100)), freshness_days=365) is None

    assert gate(Offer(id="x", source="t", title="Analyst", attribution="t")).code == "no_content"
    assert gate(Offer(id="x", source="t", title="Analyst", attribution="t", url="https://a.example.invalid/1")) is None  # title and link only
    assert gate(Offer(id="x", source="t", title="Analyst", attribution="t", skills=["sql"])) is None

    assert gate(offer(description=REQ + "- Experience deploying machine learning models to production\n- Python"), CEILING).code == "ledger_ceiling"
    g = gate(offer(description=REQ + "- We ship models into production weekly"), CEILING)
    assert g.code == "ledger_ceiling" and "ml-production-ceiling" in g.detail and "models into production" in g.quote
    soft = "Requirements\n- Python\n\nNice to have: experience deploying machine learning models to production.\n"
    assert gate(offer(description=soft), CEILING) is None  # a nice-to-have is not a requirement
    assert gate(offer(description="Nice to have\n- models to production\n\nRequirements\n- Python"), CEILING) is None
    assert gate(offer(description=REQ + "- Python and SQL"), CEILING) is None
    assert gate(offer(description=REQ + "- models to production"), None) is None  # no ledger, no ceiling
    bad = LedgerIndex(skill_backing={}, draft_skills={}, ceiling_patterns=[("x", "(unclosed")])
    assert gate(offer(description="anything"), bad) is None  # an invalid pattern is lint's job, not a crash


def test_gates_run_in_table_order():
    o = offer("Sales Representative", "This role is commission only.", valid_through=TODAY - dt.timedelta(days=3))
    assert gate(o, deny_titles=["sales*"]).code == "deny_title"
    assert gate(o).code == "commission_only"  # before expired
    assert gate(offer(description="Equity only. Unpaid trial.", valid_through=TODAY - dt.timedelta(days=3))).code == "unpaid_task"


def test_gated_offer_gets_no_percentage():
    r = score_offer(offer("Sales Rep", "This role is commission only. " + THREE), profile(skills=["python"]), config(), None, TODAY)
    assert r.gate.code == "commission_only" and r.gate.quote
    assert (r.match_pct, r.base, r.criteria, r.penalties, r.surfaced, r.low_confidence, r.band) == (0, 0.0, [], [], False, False, None)


# ---------------------------------------------------------------- penalties


def years_penalty(required, years, **kw):
    text = REQ + f"- {required}+ years of experience\n" + THREE if required else REQ + THREE
    r = score_offer(offer(description=text, **kw), profile(skills=["python", "sql", "excel"], years=years), config(), None, TODAY)
    return [(p.code, p.points) for p in r.penalties], r


def test_penalties_years_gap():
    assert years_penalty(3, 2)[0] == [("years_gap", -5)]  # gap 1
    assert years_penalty(4, 2)[0] == [("years_gap", -5)]  # gap 2
    assert years_penalty(5, 2)[0] == [("years_gap", -10)]  # gap 3: the largest matching step applies
    assert years_penalty(9, 2)[0] == [("years_gap", -10)]
    assert years_penalty(2, 2)[0] == []  # no gap
    assert years_penalty(1, 6)[0] == []  # more experience than asked
    assert years_penalty(5, None)[0] == []  # profile years unknown
    assert years_penalty(None, 2)[0] == []  # offer states no years
    codes, r = years_penalty(5, 2)
    assert r.penalties[0].quote and "5+ years" in r.penalties[0].quote
    assert years_penalty(5, 5)[1].match_pct - years_penalty(5, 2)[1].match_pct == 10
    assert years_penalty(5, 2, level="senior")[0] == [("years_gap", -10)]  # a stated level does not hide the gap


def hard(description, mine, nice=(), skills=()):
    o = offer(description=description, skills=list(skills), skills_nice=list(nice))
    return score_offer(o, profile(skills=list(mine)), config(), None, TODAY)


def test_penalties_hard_requirement_missing():
    r = hard("Tableau is required for our client dashboards. Python, SQL.", ["python", "sql"], skills=["tableau", "python", "sql"])
    assert [(p.code, p.points) for p in r.penalties] == [("hard_requirement_missing", -10)] and "Tableau" in r.penalties[0].quote
    # once, however many missing must-haves sit near a hard word
    r = hard("Tableau and dbt are required. Expert Airflow. Python, SQL.", ["python", "sql"], skills=["tableau", "dbt", "airflow", "python", "sql"])
    assert [p.code for p in r.penalties] == ["hard_requirement_missing"]
    # the skill is present, or far from any hard word, or optional
    assert hard("Tableau is required.", ["tableau"], skills=["tableau", "python", "sql"]).penalties == []
    far = "Tableau is used here. " + "x " * 80 + "Python is required. SQL."
    assert hard(far, ["python", "sql"], skills=["tableau", "python", "sql"]).penalties == []
    assert hard("Tableau is required.", [], skills=["python", "sql", "excel"], nice=["tableau"]).penalties == []  # nice-to-have never penalizes
    assert hard("Must have advanced Tableau.", ["python"], skills=["tableau", "python", "sql"]).penalties[0].code == "hard_requirement_missing"
    pl = hard("Wymagane: znajomość Tableau. Python, SQL.", ["python", "sql"], skills=["tableau", "python", "sql"])
    assert [p.code for p in pl.penalties] == ["hard_requirement_missing"]
    # the penalty is part of the match
    clean = hard("Tableau. Python, SQL.", ["python", "sql"], skills=["tableau", "python", "sql"])
    assert clean.match_pct - hard("Tableau is required. Python, SQL.", ["python", "sql"], skills=["tableau", "python", "sql"]).match_pct == 10


def test_penalties_follow_the_settings():
    no_penalties = config(scoring={"penalties": []})
    o = offer(description=REQ + "- 9+ years of experience\n" + THREE)
    assert score_offer(o, profile(years=1), no_penalties, None, TODAY).penalties == []
    assert [p.points for p in score_offer(o, profile(years=1), config(scoring={"penalties": [{"code": "years_gap", "steps": [{"min_gap": 2, "points": -7}]}]}), None, TODAY).penalties] == [-7]


# ---------------------------------------------------------------- surface, confidence, ranking


def test_surface_and_rank():
    text = REQ + "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas\n- Tableau\n"
    me = profile(skills=["python", "sql", "excel"], titles=["data analyst"], target_titles=["data analyst"])
    base = score_offer(offer(description=text), me, config(), None, TODAY)
    assert base.low_confidence is False and base.layer == "CORE"
    x = base.match_pct

    def surfaced(min_match):
        cfg = config(scoring={"surface": {"CORE": {"min_match": min_match}}})
        return score_offer(offer(description=text), me, cfg, None, TODAY).surfaced

    assert surfaced(x) is True and surfaced(x + 1) is False  # the threshold is inclusive

    # thresholds are per layer: the same percentage passes as DOWNSHIFT (45) and fails as STRETCH (raised)
    levelled = profile(skills=me.skills, titles=me.titles, target_titles=me.target_titles, level="senior", years=5)
    down = score_offer(offer("Data Analyst", text, level="junior"), levelled, config(scoring={"surface": {"DOWNSHIFT": {"min_match": 1}, "CORE": {"min_match": 100}}}), None, TODAY)
    assert down.layer == "DOWNSHIFT" and down.surfaced is True
    up = score_offer(offer("Data Analyst", text, level="lead"), levelled, config(scoring={"surface": {"STRETCH": {"min_match": 100}, "DOWNSHIFT": {"min_match": 1}}}), None, TODAY)
    assert up.layer == "STRETCH" and up.surfaced is False

    # bands only colour the result
    assert base.band == ("strong" if x >= 75 else "good" if x >= 60 else "fair")
    assert score_offer(offer(description=text), me, config(scoring={"bands": {"strong": 1, "good": 0}}), None, TODAY).band == "strong"


def test_title_only_offer_is_low_confidence_and_never_ranked():
    thin = Offer(id="thin", source="import:eml", title="Data Analyst", attribution="alerts.eml", url="https://a.example.invalid/1", thin=True)
    me = profile(skills=["python"], titles=["data analyst"], target_titles=["data analyst"], level="mid")
    r = score_offer(thin, me, config(), None, TODAY)
    assert r.low_confidence is True and r.surfaced is False and r.gate is None and r.band is None
    assert r.match_pct > 0  # shown in the report as "~NN% (title only)"
    assert best_per_offer([r]) == []
    # a described offer with fewer than 3 recognized requirements is low confidence as well
    few = score_offer(offer(description=REQ + "- Python\n- SQL"), me, config(), None, TODAY)
    assert few.low_confidence is True and few.surfaced is False
    # as is one where the criteria that apply carry too little weight
    cfg = config(scoring={"criteria": [{"id": "skills", "type": "skills_coverage", "weight": 40}, {"id": "salary", "type": "salary", "weight": 60}],
                          "min_applicable_weight": 50})  # fmt: skip
    r = score_offer(offer(description=REQ + THREE), me, cfg, None, TODAY)
    assert r.low_confidence is True and criterion_applicable(r, "salary") is False


def criterion_applicable(r, cid):
    return {c.id: c for c in r.criteria}[cid].applicable


def result(offer_id, cv_id, pct, surfaced=True) -> MatchResult:
    return MatchResult(offer_id=offer_id, cv_id=cv_id, gate=None, layer="CORE", layer_assumed=False, criteria=[], penalties=[], base=float(pct),
                       match_pct=pct, unknowns=[], requirements=[], requirements_recognized=5, backed=(0, 0), surfaced=surfaced,
                       low_confidence=False, band="good")  # fmt: skip


def test_ranking_ties_and_no_cap():
    offers = [offer(id="b", posted_at=dt.date(2026, 10, 1)), offer(id="a", posted_at=dt.date(2026, 10, 1)),
              offer(id="c", posted_at=dt.date(2026, 10, 5)), offer(id="d"), offer(id="e", posted_at=dt.date(2026, 9, 1))]  # fmt: skip
    results = [result("b", "x.md", 70), result("a", "x.md", 70), result("c", "x.md", 70), result("d", "x.md", 70), result("e", "x.md", 90)]
    ranked = best_per_offer(results, offers)
    # match first; then the newer posting; then the offer id; an undated offer comes after the dated ones
    assert [r.offer_id for r in ranked] == ["e", "c", "a", "b", "d"]
    assert [r.offer_id for r in best_per_offer(list(reversed(results)), offers)] == ["e", "c", "a", "b", "d"]  # input order does not matter
    # without the offers the posting date is unknown and the offer id decides
    assert [r.offer_id for r in best_per_offer(results)] == ["e", "a", "b", "c", "d"]

    # one row per offer: the best CV; a tie goes to the cv_id that sorts first
    several = [result("o", "z.md", 80), result("o", "a.md", 80), result("o", "m.md", 79), result("p", "a.md", 60), result("p", "b.md", 66, surfaced=False)]
    ranked = best_per_offer(several)
    assert [(r.offer_id, r.cv_id) for r in ranked] == [("o", "a.md"), ("p", "a.md")]  # an unsurfaced result is never the best CV
    assert best_per_offer([result("q", "a.md", 99, surfaced=False)]) == []

    many = [result(f"o{i:03d}", "a.md", 50 + i % 40) for i in range(120)]
    assert len(best_per_offer(many)) == 120  # no daily cap or quota


def test_score_all_returns_every_pair_in_a_fixed_order():
    text = REQ + THREE
    offers = [offer(id="b", description=text), offer(id="a", description=text)]
    profiles = [profile(cv_id="z.md", skills=["python"]), profile(cv_id="y.md", skills=["sql"])]
    results = score_all(offers, profiles, config(), None, TODAY)
    assert [(r.offer_id, r.cv_id) for r in results] == [("a", "y.md"), ("a", "z.md"), ("b", "y.md"), ("b", "z.md")]


def test_match_is_rounded_half_up_and_kept_in_range():
    tokens = [{"id": "a", "type": "tokens", "weight": 25, "tokens": ["python", "sql"], "threshold": 2},
              {"id": "b", "type": "tokens", "weight": 75, "tokens": ["cobol"], "threshold": 1}]  # fmt: skip
    r = score_offer(offer(description="We use Python."), profile(), config(scoring={"criteria": tokens}), None, TODAY)
    assert r.base == 12.5 and r.match_pct == 13  # 12.5 rounds up, not to the even number
    cfg = config(scoring={"penalties": [{"code": "hard_requirement_missing", "points": -200}]})
    o = offer(description="Tableau is required. Python, SQL.", skills=["tableau", "python", "sql"])
    assert score_offer(o, profile(skills=["python", "sql"]), cfg, None, TODAY).match_pct == 0  # never below 0


# ---------------------------------------------------------------- size and odd values


def test_scoring_reads_at_most_the_first_100000_characters_of_a_description():
    filler = "Lorem ipsum dolor sit amet. " * 4000  # 112,000 characters
    assert len(filler) > MAX_TEXT == 100_000
    late_skills = offer(description=filler + "\n" + REQ + THREE)
    early_skills = offer(description=REQ + THREE + filler)
    mine = profile(skills=["python", "sql"])
    assert extract_requirements(late_skills, mine, None) == []  # past the cap: not read
    assert [r.term for r in extract_requirements(early_skills, mine, None)] == ["python", "sql", "excel"]
    assert gate(offer(description=filler + " This role is commission only.")) is None
    assert gate(offer(description="This role is commission only. " + filler)).code == "commission_only"
    assert len(late_skills.description) > MAX_TEXT  # the offer itself is not changed


def test_a_huge_description_is_scored_quickly():
    text = "Python and SQL. " * 70000  # 1.1 million characters
    start = time.perf_counter()
    r = score_offer(offer(description=text), profile(skills=["python", "sql"]), config(), None, TODAY)
    assert time.perf_counter() - start < 2.0
    assert {q.term for q in r.requirements} == {"python", "sql"}


def test_a_salary_that_is_infinity_is_scored_as_not_stated():
    cfg = config(preferences={"min_salary": [{"amount": 20000, "currency": "USD", "unit": "month"}]})
    for amount in ("Infinity", float("nan"), "-inf"):
        o = offer(description=REQ + THREE, salary={"min": amount, "max": amount, "currency": "USD", "unit": "year"})
        r = score_offer(o, profile(skills=["python"]), cfg, None, TODAY)
        salary = next(c for c in r.criteria if c.id == "salary")
        assert salary.note == "the offer states no salary" and "salary not stated" in r.unknowns
        assert monthly_salary(o.salary, "USD") is None
