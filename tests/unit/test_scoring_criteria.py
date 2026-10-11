"""Criteria, requirement reading, layers and years."""

import datetime as dt

import pytest

from honestcv.config import Config, load_config, validate_scoring
from honestcv.errors import ConfigError
from honestcv.models import Offer, Profile, Salary
from honestcv.scoring import (assign_layer, extract_requirements, monthly_salary, offer_level, required_years, score_offer,
                              soft_spans)  # fmt: skip

TODAY = dt.date(2026, 10, 10)
REQ = "Requirements\n"


def offer(title="Data Analyst", description="", **kw) -> Offer:
    kw.setdefault("url", "https://jobs.example.invalid/1")
    return Offer(id="o1", source="test", title=title, description=description, attribution="test", **kw)


def profile(**kw) -> Profile:
    base = dict(cv_id="cv.md", cv_sha256="0" * 64, skills=[], titles=[], target_titles=[], years=None, level=None, languages={})
    return Profile(**{**base, **kw})


def config(**kw) -> Config:
    return Config.model_validate(kw)


def criterion(result, cid):
    return {c.id: c for c in result.criteria}[cid]


# ---------------------------------------------------------------- redistribution


def test_redistribution():
    text = REQ + "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas\n"
    me = profile(skills=["python", "sql"], titles=["data analyst"], target_titles=["data analyst"])
    r = score_offer(offer(description=text), me, config(), None, TODAY)
    applicable = {c.id for c in r.criteria if c.applicable}
    assert applicable == {"skills", "title"}  # no preference set, no level set, no language named
    for cid in ("location", "salary", "contract"):
        assert criterion(r, cid).note == "n/a: no preference set, weight redistributed"
    assert criterion(r, "level").note == "n/a: profile.level is not set, weight redistributed"
    assert criterion(r, "language").note.startswith("n/a: the offer names no required language")
    assert r.layer == "CORE" and r.layer_assumed is True
    assert abs(sum(c.points for c in r.criteria) - r.base) < 0.01
    assert r.base == pytest.approx(100 * (45 * (2 / 3) + 15) / 60, abs=0.01) and 0 < r.match_pct <= 100
    assert r.requirements_recognized == 3 and r.low_confidence is False  # applicable weight 60 >= 50

    # a missing salary with min_salary set scores 0 and lists the unknown
    cfg = config(preferences={"min_salary": [{"amount": 10000, "currency": "SEK", "unit": "month"}]})
    r = score_offer(offer(description=text), me, cfg, None, TODAY)
    assert criterion(r, "salary").applicable and criterion(r, "salary").fraction == 0
    assert r.unknowns == ["salary not stated"]

    # with profile.level set the level line applies; an offer that states no level scores 0 there
    r = score_offer(offer(description=text), profile(level="mid"), config(), None, TODAY)
    assert criterion(r, "level").applicable and criterion(r, "level").fraction == 0
    assert "level not stated, assumed CORE" in r.unknowns and r.layer == "CORE" and r.layer_assumed is True


def test_no_applicable_weight_is_low_confidence():
    cfg = config(scoring={"criteria": [{"id": "salary", "type": "salary", "weight": 100}]})
    r = score_offer(offer(description=REQ + "- Python"), profile(), cfg, None, TODAY)
    assert r.base == 0 and r.match_pct == 0 and r.low_confidence and not r.surfaced and r.band is None


def test_custom_tokens_criterion():
    scoring = {"criteria": [{"id": "skills", "type": "skills_coverage", "weight": 50},
                            {"id": "domain", "type": "tokens", "weight": 50, "tokens": ["logistic*", "e-commerce", "retail"], "threshold": 2}]}  # fmt: skip
    text = REQ + "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas\nWe serve retail and logistics clients.\n"
    r = score_offer(offer(description=text), profile(skills=["python"]), config(scoring=scoring), None, TODAY)
    assert criterion(r, "domain").fraction == 1.0 and criterion(r, "domain").quotes
    r = score_offer(offer(description=text.replace("retail and ", "")), profile(), config(scoring=scoring), None, TODAY)
    assert criterion(r, "domain").fraction == 0.5
    r = score_offer(offer(description=text.replace("retail and logistics", "banks")), profile(), config(scoring=scoring), None, TODAY)
    assert criterion(r, "domain").fraction == 0.0


def test_weight_validator(tmp_path):
    base = config()
    criteria = [c.model_copy() for c in base.scoring.criteria]
    criteria[0] = criteria[0].model_copy(update={"weight": 60})  # 45 -> 60: the weights now sum to 115
    bad = base.scoring.model_copy(update={"criteria": criteria})
    with pytest.raises(ConfigError) as err:
        validate_scoring(bad)
    assert "scoring.criteria weights sum to 115; they must sum to 100. Lower some weights by 15 in total." in err.value.message
    path = tmp_path / "honestcv.yml"
    path.write_text("scoring:\n  criteria:\n    - {id: skills, type: skills_coverage, weight: 60}\n    - {id: title, type: title_match, weight: 55}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_config(path)
    assert "sum to 115" in err.value.message and err.value.line == 2


# ---------------------------------------------------------------- required years


@pytest.mark.parametrize(
    ("text", "years"),
    [
        (REQ + "- minimum of 3 years", 3),
        (REQ + "- Minimum 4 years in a similar role", 4),
        ("- 3-5 years of experience", 3),
        ("- 3 - 5 years", 3),
        (REQ + "- 2 years of experience", 2),
        (REQ + "- 1 year of experience", 1),
        (REQ + "- min. rok doświadczenia", 1),
        (REQ + "- min. 3 lata doświadczenia", 3),
        (REQ + "- co najmniej 2 lata", 2),
        ("Wymagania:\n- od 4 lat w branży", 4),
        ("Wymagania:\n- dwa lata doświadczenia", 2),
        (REQ + "- at least five years", 5),
        (REQ + "- three years of experience", 3),
        (REQ + "- 5+ years", 5),
        (REQ + "- 3 or more years", 3),
        (REQ + "- 2 years of relevant experience", 2),
        (REQ + "- 2+ years\n- 6+ years of experience with data", 6),  # the maximum
        ("- 4+ years in data", 4),  # a bullet without a heading
        ("We are looking for you. You bring 7 years of experience.", 7),  # a sentence that addresses the reader
        ("About us: 12 years of experience in logistics.\nRequirements\n- 2 years of experience", 2),  # only after the heading
        (REQ + "- ten years of experience", 10),
        (REQ + "- 15+ years", 15),
    ],
)
def test_required_years(text, years):
    assert required_years(text) == years


@pytest.mark.parametrize(
    "text",
    [
        "We are a company with 20 years of experience in logistics.",  # no heading, no bullet, no 'you'
        "Founded 12 years ago. Our founders have 11 years of experience.",
        REQ + "- 20+ years of experience",  # above 15 is discarded
        REQ + "- 16 years of experience",
        REQ + "- Python and SQL",
        "",
    ],
)
def test_required_years_none(text):
    assert required_years(text) is None


# ---------------------------------------------------------------- level and layer


def test_layer():
    me = profile(level="mid", years=4)
    layer = lambda title, desc="", **kw: assign_layer(offer(title, desc, **kw), me)  # noqa: E731
    assert layer("Office Assistant Intern") == ("DOWNSHIFT", False)
    assert offer_level(offer("International Sales Coordinator")) is None
    assert offer_level(offer("Internal Tools Developer")) is None
    assert layer("International Sales Coordinator") == ("CORE", True)  # not read as intern; level unknown
    assert layer("Senior Data Analyst") == ("STRETCH", False)
    assert layer("Data Analyst", level="senior") == ("STRETCH", False)  # the level a source states
    assert layer("Junior Data Analyst") == ("DOWNSHIFT", False)
    assert layer("Mid-level Data Analyst") == ("CORE", False)
    # STRETCH is evaluated before DOWNSHIFT: a junior title that asks for 7+ years of a 4-year profile is a stretch
    assert layer("Junior Analyst", REQ + "- 7+ years of experience") == ("STRETCH", False)
    assert layer("Analyst", REQ + "- 6+ years of experience") == ("STRETCH", False)  # senior band
    assert layer("Lead Generation Specialist") == ("CORE", True)  # "lead generation" is not a lead role
    assert offer_level(offer("Team Lead, Support")) == "lead" and offer_level(offer("Mid-market Account Manager")) is None
    assert offer_level(offer("Analityk młodszy")) == "junior" and offer_level(offer("Starszy analityk")) == "senior"
    assert offer_level(offer("Staż w dziale HR")) == "intern" and offer_level(offer("Staff Accountant")) is None
    # no profile.level: the layer is CORE and assumed
    assert assign_layer(offer("Senior Data Analyst"), profile()) == ("CORE", True)


@pytest.mark.parametrize(
    ("years", "level"),
    [(0, "junior"), (1, "junior"), (2, "mid"), (4, "mid"), (5, "senior"), (7, "senior"), (8, "lead"), (12, "lead")],
)
def test_year_bands_are_half_open(years, level):
    assert offer_level(offer("Analyst", REQ + f"- {years}+ years of experience")) == level


def test_years_rule_needs_a_gap_of_three():
    two_years = profile(level="senior", years=2)
    assert assign_layer(offer("Analyst", REQ + "- 5+ years"), two_years) == ("STRETCH", False)  # 5 - 2 = 3
    assert assign_layer(offer("Analyst", REQ + "- 4+ years"), two_years) == ("DOWNSHIFT", False)  # mid < senior, gap 2
    assert assign_layer(offer("Analyst", REQ + "- 5+ years"), profile(level="senior", years=None)) == ("CORE", False)


# ---------------------------------------------------------------- title


def test_title_generic_tokens():
    me = profile(titles=["data analyst"], target_titles=["data analyst"])

    def fraction(title):
        return criterion(score_offer(offer(title, REQ + "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas"), me, config(), None, TODAY), "title").fraction

    assert fraction("Support Analyst") == 0  # shares only the generic "analyst"
    assert fraction("Data Analyst") == 1.0
    assert fraction("Senior Data Analyst") == 1.0  # level words are ignored
    assert fraction("Data Engineer") == 0.5  # one non-generic word in common, threshold 2
    assert fraction("Pastry Chef") == 0
    pl = profile(titles=["Analityk danych"], target_titles=["analityk danych"])
    r = score_offer(offer("Analityk danych (k/m)", REQ + "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas"), pl, config(), None, TODAY)
    assert criterion(r, "title").fraction == 1.0
    assert criterion(score_offer(offer(), profile(), config(), None, TODAY), "title").applicable is False  # no titles at all


# ---------------------------------------------------------------- location


def location(prefs, **kw):
    r = score_offer(offer(**kw), profile(), config(preferences=prefs), None, TODAY)
    return criterion(r, "location"), r.unknowns


def test_location():
    c, _ = location({"countries": ["PL"]}, countries=["PL"], remote="onsite")  # countries alone can reach 1.0
    assert c.fraction == 1.0
    c, _ = location({"countries": ["PL"]}, location="Anywhere", remote="remote")
    assert c.fraction == 1.0  # the place side is satisfied by "open worldwide"
    c, unknown = location({"remote": ["remote"], "countries": ["PL"]}, remote="remote")
    assert c.fraction == 0.5 and unknown == ["remote, country rule not stated"]
    c, unknown = location({"remote": ["remote"], "countries": ["PL"]}, remote="remote", countries=["SE"], location="Remote, Sweden")
    assert c.fraction == 0.5 and unknown == []  # mode matches, place does not
    c, _ = location({"remote": ["remote"], "countries": ["PL"]}, remote="onsite", countries=["SE"])
    assert c.fraction == 0.0
    c, unknown = location({"remote": ["remote"]})
    assert c.fraction == 0.0 and unknown == ["work mode and location not stated"]
    c, _ = location({"cities": ["Gothenburg"]}, location="Gothenburg, Sweden", remote="hybrid")
    assert c.fraction == 1.0
    c, _ = location({}, remote="remote")
    assert c.applicable is False


# ---------------------------------------------------------------- skills


def terms(description, me=None, **kw):
    return {r.term: r for r in extract_requirements(offer(description=description, **kw), me or profile(), None)}


def test_skills_ambiguous():
    assert terms("We make an impact. The rest of the team is ready to go. Come and excel with us.") == {}
    found = terms("Use make.com and a REST API; Advanced Excel formulas help.")
    assert set(found) == {"make", "rest api", "excel"}
    structured = terms("", skills=["Make", "Go", "R"], skills_nice=["k8s"])
    assert set(structured) == {"make", "go", "r", "kubernetes"} and structured["kubernetes"].must is False  # canonical names
    assert terms("Python is not required. SQL is a must.").keys() == {"sql"}
    assert terms("No Python needed. We use SQL.").keys() == {"sql"}
    assert terms("Python, but not only Python: SQL too.").keys() == {"python", "sql"}  # "not only" is not a negation
    assert terms("Looker Studio dashboards.").keys() == {"looker studio"}  # not also "looker"
    assert terms("Experience with C++ and C#.").keys() == {"c++", "c#"}  # not also the ambiguous "c"
    r = score_offer(offer(description="We need Python and SQL."), profile(skills=["python", "sql"]), config(), None, TODAY)
    assert r.requirements_recognized == 2 and r.low_confidence is True and r.surfaced is False  # fewer than 3 recognized terms


def test_requirements_reading_marks_what_the_profile_has():
    me = profile(skills=["python", "my tool"])
    found = terms(REQ + "- Python\n- SQL\n- uses My Tool daily", me)
    assert {t: (r.must, r.in_profile) for t, r in found.items()} == {"python": (True, True), "sql": (True, False), "my tool": (True, True)}
    assert found["python"].quote and "Python" in found["python"].quote and found["sql"].backed_by == []


def test_soft_word_window():
    found = terms("Nice to have: dbt. Requirements: Python")
    assert found["python"].must is True and found["dbt"].must is False
    assert terms("Nice to have: Tableau\nPython is required")["tableau"].must is False
    assert terms("Nice to have: Tableau\nPython is required")["python"].must is True
    sectioned = terms("Requirements\n- SQL\n- Python\n\nNice to have\n- dbt\n- Airflow\n\nBenefits\n- Free coffee and Excel training")
    assert {t: r.must for t, r in sectioned.items() if t in ("sql", "python", "dbt", "airflow")} == {"sql": True, "python": True, "dbt": False, "airflow": False}
    assert terms("Mile widziane: Tableau. Wymagane: SQL")["tableau"].must is False
    assert terms("Mile widziane: Tableau. Wymagane: SQL")["sql"].must is True
    assert terms("Dodatkowym atutem będzie znajomość dbt.\nSQL jest wymagany.")["dbt"].must is False
    low = "nice to have: dbt. requirements: python"
    assert soft_spans(low)[0][0] == 0 and not any(a <= low.index("python") < b for a, b in soft_spans(low))
    assert terms("Nice to have: pandas, Tableau.")["pandas"].must is False


# ---------------------------------------------------------------- language


def language(text, languages=None, mine=None):
    r = score_offer(offer(description=REQ + "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas\n" + text, languages=languages or []),
                    profile(languages=mine or {}), config(), None, TODAY)  # fmt: skip
    return criterion(r, "language")


def test_language_cue():
    assert language("We polish the dashboards and polish our reports.").applicable is False  # the verb, not the language
    c = language("Fluent Polish is needed.", mine={"pl": "native"})
    assert c.applicable and c.fraction == 1.0
    assert language("Fluent Polish is needed.", mine={"en": "C1"}).fraction == 0.0
    assert language("Fluent English required.", mine={"en": "C1"}).fraction == 1.0
    assert language("We like Polish pastries.").applicable is False  # no requirement cue within 60 characters
    c = language("Znajomość języka angielskiego wymagana.", mine={"en": "B2"})
    assert c.applicable and c.fraction == 1.0
    assert language("", languages=["en", "sv"], mine={"en": "C1"}).fraction == 0.0  # structured languages count as required
    assert language("", languages=["en", "sv"], mine={"en": "C1", "sv": "native"}).fraction == 1.0
    assert language("Polish required, English a plus.", mine={"pl": "native"}).applicable


# ---------------------------------------------------------------- salary


@pytest.mark.parametrize(
    ("salary", "currency", "monthly"),
    [
        (Salary(max=60, currency="SEK"), "SEK", 9600),  # below 500: per hour
        (Salary(max=45, currency="EUR", unit="hour"), "EUR", 7200),
        (Salary(max=800, currency="EUR", unit="day"), "EUR", 16000),
        (Salary(max=60000, currency="EUR", unit="year"), "EUR", 5000),
        (Salary(max=60000, currency="EUR"), "EUR", 5000),  # 20,000 or more in EUR: per year
        (Salary(max=30000, currency="USD"), "USD", 2500),
        (Salary(max=1500, currency="EUR"), "EUR", None),  # 500 to 2,500 in EUR: unit unknown
        (Salary(max=2500, currency="GBP"), "GBP", None),
        (Salary(max=5000, currency="CHF"), "CHF", 5000),  # otherwise per month
        (Salary(max=3000, currency="PLN"), "PLN", None),  # 500 to 4,000 in PLN: unknown
        (Salary(max=4001, currency="PLN"), "PLN", 4001),
        (Salary(max=12000, currency="PLN"), "PLN", 12000),
        (Salary(max=30000, currency="SEK"), "SEK", 30000),
        (Salary(min=40000, max=48000, currency="SEK", unit="month"), "SEK", 48000),  # the highest figure
        (Salary(min=40000, currency="SEK", unit="month"), "SEK", 40000),  # a minimum only
        (Salary(max=48000, currency="SEK", unit="month"), "EUR", None),  # another currency
        (Salary(currency="SEK", unit="month"), "SEK", None),
    ],
)
def test_monthly_salary(salary, currency, monthly):
    assert monthly_salary(salary, currency) == monthly


def salary_result(salary, minimum=None):
    prefs = {"min_salary": minimum or [{"amount": 5000, "currency": "EUR", "unit": "month"}]}
    r = score_offer(offer(description=REQ + "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas", salary=salary), profile(), config(preferences=prefs), None, TODAY)
    return criterion(r, "salary"), r.unknowns


def test_salary_units():
    c, unknown = salary_result(Salary(max=60000, currency="EUR"))
    assert c.fraction == 1.0 and unknown == ["salary unit assumed: year"]
    c, unknown = salary_result(Salary(max=36, currency="EUR"))
    assert c.fraction == 1.0 and unknown == ["salary unit assumed: hour"]  # 36 x 160 = 5,760
    c, unknown = salary_result(Salary(max=1500, currency="EUR"))
    assert c.applicable and c.fraction == 0.0 and unknown == ["salary unit not stated"]
    c, unknown = salary_result(Salary(max=4200, currency="EUR", unit="month"))
    assert c.fraction == 0.5 and unknown == []  # at least 80% of the minimum
    c, _ = salary_result(Salary(max=3900, currency="EUR", unit="month"))
    assert c.fraction == 0.0
    c, unknown = salary_result(Salary(max=5000, currency="USD", unit="month"))  # no USD minimum: not applicable for this offer
    assert c.applicable is False and unknown == ["salary in USD; you set no USD minimum"]
    c, unknown = salary_result(None)
    assert c.applicable and c.fraction == 0.0 and unknown == ["salary not stated"]
    c, _ = salary_result(Salary(max=40, currency="EUR", unit="hour"), minimum=[{"amount": 30, "currency": "EUR", "unit": "hour"}])
    assert c.fraction == 1.0  # a minimum may use any unit
    c, _ = salary_result(Salary(max=400, currency="PLN", unit="day"), minimum=[{"amount": 5000, "currency": "EUR"}, {"amount": 7000, "currency": "PLN"}])
    assert c.fraction == 1.0  # 400 x 20 = 8,000 PLN a month against the PLN entry


def test_contract():
    prefs = {"contract": ["b2b", "employment"]}
    text = REQ + "- Python\n- SQL\n- Excel dashboards and advanced Excel formulas"
    for contract, fraction in ((["employment"], 1.0), (["freelance"], 0.0), ([], 0.0)):
        r = score_offer(offer(description=text, contract=contract), profile(), config(preferences=prefs), None, TODAY)
        assert criterion(r, "contract").fraction == fraction
    assert r.unknowns == ["contract form not stated"]
