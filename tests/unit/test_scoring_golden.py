"""The frozen worked example and the sample-offer expectations (tests/golden/scoring_expect.yml)."""

import datetime as dt
import json
import random

import pytest
import yaml

from honestcv.config import load_config
from honestcv.models import LedgerIndex, Offer, Salary
from honestcv.profile import extract_profile
from honestcv.scoring import best_per_offer, score_all

TODAY = dt.date(2026, 10, 10)


def offer_from_sample(raw: dict) -> Offer:
    """The sample JSON as an Offer, built here so the tests do not depend on the importers."""
    salary = None
    if raw.get("currency"):
        salary = Salary(min=raw.get("salary_min"), max=raw.get("salary_max"), currency=raw["currency"], unit=raw.get("salary_unit"))
    return Offer(
        id=raw["requisition_id"], source="import:json", title=raw["title"], company=raw.get("company"), url=raw.get("url"),
        location=raw.get("location"), countries=raw.get("countries", []), remote=raw.get("remote"), contract=raw.get("contract", []),
        skills=raw.get("skills", []), skills_nice=raw.get("skills_nice", []), languages=raw.get("languages", []), salary=salary,
        description=raw.get("description", ""), posted_at=raw.get("posted_at"), valid_through=raw.get("valid_through"),
        requisition_id=raw["requisition_id"], attribution="the sample file",
    )  # fmt: skip


@pytest.fixture
def world(repo_root):
    expect = yaml.safe_load((repo_root / "tests" / "golden" / "scoring_expect.yml").read_text(encoding="utf-8"))
    cfg = load_config(repo_root / expect["config"])
    raw = json.loads((repo_root / expect["offers"]).read_text(encoding="utf-8"))["offers"]
    offers = [offer_from_sample(r) for r in raw]
    profiles = [extract_profile(repo_root / p, cfg.profile, today=TODAY) for p in expect["cvs"]]
    index = LedgerIndex(**expect["ledger_index"])
    results = score_all(offers, profiles, cfg, index, TODAY)
    by = {(r.offer_id, r.cv_id): r for r in results}
    return {"expect": expect, "cfg": cfg, "offers": offers, "profiles": profiles, "index": index, "results": results, "by": by}


def test_worked_example(world):
    ex = world["expect"]["worked_example"]
    r = world["by"][(ex["offer"], ex["cv"])]
    assert r.gate is None and r.layer == ex["layer"] and r.layer_assumed is ex["layer_assumed"]
    assert abs(r.base - ex["base"]) <= ex["base_tolerance"]
    assert r.match_pct == ex["match_pct"] == 83
    assert (r.band, r.surfaced, r.low_confidence) == (ex["band"], ex["surfaced"], ex["low_confidence"])
    assert r.requirements_recognized == ex["requirements_recognized"]
    assert r.backed == tuple(ex["backed"]) == (4, 5)
    assert [p.code for p in r.penalties] == ex["penalties"] and r.unknowns == ex["unknowns"]

    got = {c.id: c for c in r.criteria}
    assert [c.id for c in r.criteria] == [c["id"] for c in ex["criteria"]]  # config order
    applicable_weight = sum(c.weight for c in r.criteria if c.applicable)
    assert applicable_weight == ex["applicable_weight"] == 95
    for want in ex["criteria"]:
        c = got[want["id"]]
        assert (c.weight, c.applicable) == (want["weight"], want["applicable"]), want["id"]
        assert abs(c.points - want["points"]) <= ex["points_tolerance"], want["id"]
        if want["applicable"]:
            assert abs(c.fraction - want["fraction"]) <= ex["fraction_tolerance"], want["id"]
    assert got["contract"].note == "n/a: no preference set, weight redistributed"
    assert abs(sum(c.points for c in r.criteria) - r.base) < 0.01  # the points shown add up to the base
    assert got["skills"].note == "4 of 6 required skills, 1 of 2 nice-to-have. Missing: airflow, dbt, tableau."

    reqs = {q.term: q for q in r.requirements}
    assert set(reqs) == {q["term"] for q in ex["requirements"]}
    for want in ex["requirements"]:
        q = reqs[want["term"]]
        assert (q.must, q.in_profile, sorted(q.backed_by)) == (want["must"], want["in_profile"], sorted(want["backed_by"])), want["term"]
    missing = [q.term for q in r.requirements if q.must and not q.in_profile]
    assert sorted(missing) == sorted(ex["missing_required"])
    assert [q.term for q in r.requirements if not q.must and not q.in_profile] == ex["missing_nice"]


def test_sample_offer_expectations(world):
    by, ex = world["by"], world["expect"]
    for want in ex["offers_expect"]:
        for cv in world["profiles"]:
            r = by[(want["offer"], cv.cv_id)]
            assert (r.gate.code if r.gate else None) == want["gate"], (want["offer"], cv.cv_id)
            if "gate_detail_contains" in want:
                assert want["gate_detail_contains"] in r.gate.detail and r.gate.quote
            if "layer" in want and want["gate"] is None and "best_cv" not in want:
                assert r.layer == want["layer"], (want["offer"], cv.cv_id)
        if "best_cv" in want:
            best = [x for x in best_per_offer(world["results"], world["offers"]) if x.offer_id == want["offer"]]
            assert [x.cv_id for x in best] == [want["best_cv"]]
            assert best[0].layer == want["layer"]
            for cid, value in want.get("criteria_fraction", {}).items():
                assert {c.id: c for c in best[0].criteria}[cid].fraction == value
        if "skills_fraction_strictly_higher" in want:
            rule = want["skills_fraction_strictly_higher"]
            frac = lambda cv: {c.id: c for c in by[(want["offer"], cv)].criteria}["skills"].fraction  # noqa: E731
            assert frac(rule["cv"]) > frac(rule["than"])
        if want["gate"] is None:
            assert by[(want["offer"], "maya-analytics.md")].surfaced is want["surfaced"]


def test_ranking_and_gate_counts(world):
    ex = world["expect"]["ranking"]
    ranked = best_per_offer(world["results"], world["offers"])
    assert ranked[0].offer_id == ex["first"] and ranked[0].match_pct == 83
    assert {r.offer_id for r in ranked} == set(ex["surfaced"])
    assert [r.offer_id for r in ranked if r.low_confidence] == ex["low_confidence"] == []
    assert not any(r.low_confidence for r in world["results"])
    counts: dict[str, int] = {}
    for r in world["results"]:
        if r.gate and r.cv_id == "maya-analytics.md":
            counts[r.gate.code] = counts.get(r.gate.code, 0) + 1
    assert counts == ex["gate_counts"]
    # the hand calculation kept in the golden file (not asserted there) agrees with the code
    for offer_id, want in world["expect"]["illustrative"].items():
        r = world["by"][(offer_id, want["best_cv"])]
        assert r.match_pct == want["match_pct"], offer_id
        assert [p.code for p in r.penalties] == want.get("penalties", []), offer_id
    assert [r.match_pct for r in ranked] == sorted((r.match_pct for r in ranked), reverse=True)


def test_scoring_repeatable(world):
    first = json.dumps([r.model_dump(mode="json") for r in world["results"]], sort_keys=True)
    again = score_all(world["offers"], world["profiles"], world["cfg"], world["index"], TODAY)
    assert json.dumps([r.model_dump(mode="json") for r in again], sort_keys=True) == first
    offers, profiles = list(world["offers"]), list(world["profiles"])
    random.Random(7).shuffle(offers)
    random.Random(8).shuffle(profiles)
    shuffled = score_all(offers, profiles, world["cfg"], world["index"], TODAY)
    assert json.dumps([r.model_dump(mode="json") for r in shuffled], sort_keys=True) == first  # input order does not matter
    assert [r.offer_id for r in best_per_offer(shuffled, offers)] == [r.offer_id for r in best_per_offer(world["results"], world["offers"])]
