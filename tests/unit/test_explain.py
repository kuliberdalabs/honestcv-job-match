"""The rubric text is generated from the settings, so the documentation cannot drift from the code."""

import re

from honestcv.config import Config
from honestcv.explain import render_rubric

FRAGMENTS = ("scr" + "ap", "byp" + "ass", "stea" + "lth", "du" + "mp", "le" + "ak", "cl" + "one", "expl" + "oit", "arbit" + "rage", "cr" + "ack", "un" + "lock", "cr" + "awl", "harv" + "est")


def test_rubric_render(repo_root):
    committed = (repo_root / "docs" / "scoring.md").read_text(encoding="utf-8")
    assert render_rubric(Config()) == committed  # regenerate docs/scoring.md with render_rubric(Config()) after any change to the rubric


def test_rubric_follows_the_settings():
    cfg = Config.model_validate({
        "preferences": {"freshness_days": 14},
        "scoring": {
            "criteria": [{"id": "skills", "type": "skills_coverage", "weight": 60, "nice_to_have_credit": 0.25},
                         {"id": "domain", "type": "tokens", "weight": 40, "tokens": ["retail", "logistic*"], "threshold": 2}],
            "penalties": [{"code": "hard_requirement_missing", "points": -20}],
            "surface": {"CORE": {"min_match": 65}, "STRETCH": {"min_match": 55}, "DOWNSHIFT": {"min_match": 40}},
            "bands": {"strong": 90, "good": 70},
            "min_applicable_weight": 70,
        },
    })  # fmt: skip
    text = render_rubric(cfg)
    assert "| skills | skills_coverage | 60 |" in text and "| domain | tokens | 40 |" in text
    assert "count 0.25 each" in text and "retail, logistic*" in text and "2 or more" in text
    assert "CORE 65, STRETCH 55, DOWNSHIFT 40" in text and "90 and above is strong, 70 and above is good" in text
    assert "less than 70 of the total weight" in text and "more than 14 days ago" in text
    assert "`hard_requirement_missing`: 20 points" in text and "years_gap" not in text
    assert "| salary |" not in text


def test_rubric_is_plain_english():
    text = render_rubric(Config())
    assert "\u2014" not in text and "\u2013" not in text  # no em or en dashes
    assert not any(word in text.lower() for word in FRAGMENTS)
    assert text.endswith("\n") and "\r" not in text
    assert "It is not a hire probability." in text
    assert re.search(r"Ledger backing is shown beside it and does not change it", text)
