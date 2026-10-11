"""Text from outside never reaches the terminal with control characters in it; amounts that are not numbers mean "not stated"."""

import json
import unicodedata

import pytest
from pydantic import ValidationError

from honestcv.config import load_config
from honestcv.errors import ConfigError
from honestcv.importers.urls import make_salary
from honestcv.models import Money, Offer, Salary, ScanRun, strip_controls
from honestcv.report.matches import salary_text
from honestcv.textutil import normalize, plain

OSC_TITLE = "\x1b]52;c;RkFLRQ==\x07Data Analyst"  # sets the clipboard in terminals that allow it
CSI_COMPANY = "Evil\x1b[2J Co"  # clears the screen

ALL_CONTROLS = [chr(c) for c in (*range(0x20), 0x7F, *range(0x80, 0xA0))]


def controls_in(text: str) -> list[str]:
    return [c for c in text if unicodedata.category(c) == "Cc" and c not in "\t\n"]


def strings(node):
    """Every string in a dumped model: values, list items and nested dicts."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from strings(value)


def offer(**kw) -> Offer:
    base = dict(id="o1", source="test", title="Data Analyst", attribution="test")
    return Offer(**{**base, **kw})


# ---------------------------------------------------------------- normalize and plain


def test_normalize_removes_every_control_character_but_tab_and_newline():
    for c in ALL_CONTROLS:
        out = normalize(f"a{c}b")
        assert controls_in(out) == [], repr(c)
        assert out in ("ab", "a\nb", "a\tb"), repr(c)
    assert normalize("a\tb\nc") == "a\tb\nc"
    assert normalize("one\r\ntwo\rthree") == "one\ntwo\nthree"  # a carriage return is a line end
    assert normalize("\x7fx\x85y\x9fz") == "xyz"
    assert normalize("\x1b[2J\x1b[0;0HSenior Eng") == "[2J[0;0HSenior Eng"
    assert normalize(OSC_TITLE) == "]52;c;RkFLRQ==Data Analyst"


def test_normalize_leaves_polish_and_swedish_text_alone():
    text = "Zażółć gęślą jaźń. Łódź, Kraków, Gdańsk.\nÅre, Göteborg, Malmö, Östersund, Västerås.\tÄ ä Ö ö Å å"
    assert normalize(text) == text
    assert strip_controls(text) == text


def test_plain_removes_control_characters_from_text_and_from_html():
    assert plain("Senior\x1b[2J Engineer\x07 wanted") == "Senior[2J Engineer wanted"
    assert controls_in(plain("<p>Data\x1b[31m Analyst\x00</p><ul><li>SQL\x9b</li></ul>")) == []
    assert plain("<p>Data\x1b[31m Analyst</p>") == "Data[31m Analyst"
    assert controls_in(plain("<p>Row&#27;[2J and&#7; more&#128; text</p>")) == []
    assert plain("<p>Zażółć gęślą jaźń</p><p>Göteborg</p>") == "Zażółć gęślą jaźń\nGöteborg"


# ---------------------------------------------------------------- the Offer model


def test_an_offer_built_anywhere_comes_out_without_control_characters():
    o = offer(title=OSC_TITLE, company=CSI_COMPANY)
    assert (o.title, o.company) == ("]52;c;RkFLRQ==Data Analyst", "Evil[2J Co")

    dirty = "\x1b[2J\x00\x07"
    o = offer(
        title=f"Data{dirty} Analyst", company=f"Tall{dirty}grove", url=f"https://jobs.example.invalid/1{dirty}", apply_url=f"https://jobs.example.invalid/2{dirty}",
        other_urls=[f"https://jobs.example.invalid/3{dirty}", "https://jobs.example.invalid/4"], location=f"Gothen{dirty}burg", countries=[f"S{dirty}E"],
        skills=[f"sql{dirty}", "python"], skills_nice=[f"excel{dirty}"], languages=[f"e{dirty}n"], description=f"Line one{dirty}\r\nLine two\rLine three\tTabbed",
        requisition_id=f"R-{dirty}1", attribution=f"Remote{dirty}OK", possible_duplicate_of=f"abc{dirty}", source=f"imp{dirty}ort:json",
        salary={"min": 1000, "max": 2000, "currency": f"S{dirty}EK", "unit": "month"},
    )  # fmt: skip
    dumped = o.model_dump(mode="json")
    assert controls_in("".join(strings(dumped))) == []
    assert o.description == "Line one[2J\nLine two\nLine three\tTabbed"
    assert (o.skills, o.skills_nice, o.other_urls[0]) == (["sql[2J", "python"], ["excel[2J"], "https://jobs.example.invalid/3[2J")
    assert o.salary.currency == "S[2JEK"


def test_an_offer_read_from_json_is_cleaned_too():
    raw = {"id": "o1", "source": "import:json", "title": OSC_TITLE, "company": CSI_COMPANY, "attribution": "your file alerts.json",
           "description": "Hello\x1b[2J", "skills": ["sql\x07"], "salary": {"min": 1, "currency": "E\x1bUR"}}  # fmt: skip
    for o in (Offer.model_validate_json(json.dumps(raw)), Offer.model_validate(raw)):
        assert controls_in("".join(strings(o.model_dump(mode="json")))) == []
        assert (o.title, o.company, o.skills, o.salary.currency) == ("]52;c;RkFLRQ==Data Analyst", "Evil[2J Co", ["sql"], "EUR")

    run = {"schema": "honestcv.scan.v1", "run_id": "r", "today": "2026-10-10", "status": "PASS", "sources": [], "cvs": [], "offers": [raw],
           "results": [], "gate_counts": {}, "new_offer_ids": [], "config_digest": "0", "ledger_present": False}  # fmt: skip
    assert ScanRun.model_validate(run).offers[0].title == "]52;c;RkFLRQ==Data Analyst"


def test_the_offer_guard_leaves_normal_text_and_instances_alone():
    text = "Analityk danych (k/m)\tŁódź\nVästerås"
    o = offer(title=text, company="Göteborg Åre AB", location="Kraków, Polska", description=text * 3)
    assert (o.title, o.company, o.location, o.description) == (text, "Göteborg Åre AB", "Kraków, Polska", text * 3)
    assert Offer.model_validate(o) is o


# ---------------------------------------------------------------- amounts that are not numbers


@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "inf", "nan", "NaN", float("inf"), float("-inf"), float("nan"), "1e999"])
def test_a_salary_amount_that_is_not_finite_is_not_stated(value):
    s = Salary(min=value, max=value, currency="USD", unit="year")
    assert (s.min, s.max, s.currency, s.unit) == (None, None, "USD", "year")
    half = Salary(min=value, max=5000)
    assert (half.min, half.max) == (None, 5000)
    o = offer(salary={"min": value, "max": 90000, "currency": "USD"})
    assert (o.salary.min, o.salary.max) == (None, 90000)


def test_infinity_from_a_feed_or_a_file_neither_fails_nor_overflows():
    s = make_salary("Infinity", "inf", "usd", "year")
    assert s is not None and (s.min, s.max) == (None, None)
    o = offer(salary=s)
    assert salary_text(o) is None
    assert salary_text(offer(salary=make_salary("Infinity", 90000, "usd", "year"))) == "90,000 USD/year"
    assert json.loads(o.model_dump_json())["salary"]["min"] is None
    assert Salary(min=1500.5, max=2000).max == 2000 and Salary().min is None


def test_a_minimum_salary_in_the_config_must_be_finite(tmp_path):
    for value in (float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            Money(amount=value, currency="PLN")
    assert Money(amount=12000, currency="pln").currency == "PLN"
    path = tmp_path / "honestcv.yml"
    path.write_text("preferences:\n  min_salary: [{amount: .inf, currency: PLN}]\n", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_config(path)
    assert "min_salary" in err.value.message and err.value.line == 2
