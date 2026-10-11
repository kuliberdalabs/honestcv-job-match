"""Renders the scoring rubric from the config (docs/scoring.md). The only prose copy of the rubric: it is built from
the settings and the data packs, so the documentation cannot drift from the code."""

from honestcv.config import Config, Criterion
from honestcv.profile import pack

_RULES = {
    "skills_coverage": "Counts the skills the offer asks for that your CV has. Required skills count fully; nice-to-have skills count {nice_to_have_credit:g} each. "
    "The fraction is (required matched + {nice_to_have_credit:g} x nice-to-have matched) divided by (required + {nice_to_have_credit:g} x nice-to-have). "
    "When the offer has no skill list, honestcv reads its text. A skill is nice-to-have when a word such as nice to have or a plus comes before it in the same sentence, or under a heading like that. "
    "A skill the text rules out (\"Python is not required\") is ignored, and everyday words such as go, make and rest count only in a clear form such as golang or make.com. "
    "The line does not apply when the offer names no skill honestcv knows. Only skills on honestcv's list are recognized; requirements it does not know are not counted as missing.",
    "title_match": "Compares the offer title with your target titles, word by word, ignoring level words such as senior. Full score when at least {threshold} words match "
    "(or every word of a shorter target title). Half score for one matching word. Words that name a kind of job (analyst, manager, specialist) count only beside a shared word that names a field. "
    "The line does not apply when your profile has no job titles.",
    "level": "Scores the layer of the offer (see Layers): CORE {points[CORE]:g}, STRETCH {points[STRETCH]:g}, DOWNSHIFT {points[DOWNSHIFT]:g}. "
    "The line does not apply until profile.level is set. An offer that states no level scores 0 and is listed as unknown.",
    "location": "Two checks. Work mode: the offer's mode is in preferences.remote, or you set none. Place: the offer's country or city is in your list, the offer is open worldwide, or you set none. "
    "Both pass: full score. One passes: half. A remote offer that states no country rule gets half and an unknown. The line applies only if you set remote, countries or cities.",
    "salary": "Compares the offer's highest stated pay, converted to a month (hour x 160, day x 20, year / 12), with your minimum in the same currency. "
    "At or above the minimum: full score. At least {near_ratio:.0%} of it: half. Below: 0. The line applies only if you set a minimum in the offer's currency. "
    "An offer without pay scores 0 and is listed as unknown. When the unit is missing, honestcv infers it from the amount and says so; if it cannot, the line scores 0.",
    "contract": "Full score when the offer's contract form is in preferences.contract. 0 when it is not, or when the offer states none. The line applies only if you set contract forms.",
    "language": "Full score when your CV lists every language the offer requires. A language counts as required when the source lists it, or when the text names it within 60 characters of a cue such as fluent or required. "
    "CEFR levels are shown, not compared. The line does not apply when the offer names no language.",
    "tokens": "Counts how many of these words or phrases appear in the offer: {tokens}. Full score at {threshold} or more, half score for fewer, 0 for none.",
}


def _rule(c: Criterion) -> str:
    points = {layer: c.points.get(layer, 1.0 if layer == "CORE" else 0.5) for layer in ("CORE", "STRETCH", "DOWNSHIFT")}
    return _RULES.get(c.type, "").format(**{**c.model_dump(), "tokens": ", ".join(c.tokens), "points": points})


def render_rubric(cfg: Config) -> str:
    """Markdown for docs/scoring.md, derived from the scoring settings and the data packs."""
    sc, levels, gates = cfg.scoring, pack("levels.yml"), pack("gates.yml")
    surface = ", ".join(f"{layer} {sc.surface.min_match(layer)}" for layer in ("CORE", "STRETCH", "DOWNSHIFT"))  # type: ignore[arg-type]
    lines = [
        "# How honestcv scores an offer", "",
        "This page is generated from the default settings by `render_rubric`. Do not edit it by hand.",
        "Change the `scoring:` block of honestcv.yml to change the numbers.", "",
        "Match % = how much of this offer your CV covers, by the criteria in your honestcv.yml. It is not a hire probability.", "",
        "## The percentage", "",
        "Each offer is scored against each CV in `cvs/`. The card shows the best CV.",
        "Each criterion has a weight and a fraction between 0 and 1. A criterion that does not apply is left out and its weight is shared out in proportion (see below).", "",
        "- base = 100 x the sum of weight x fraction over the criteria that apply, divided by the sum of their weights.",
        "- match = base plus the penalties, rounded half up, kept between 0 and 100.",
        "- No model changes the number. Ledger backing is shown beside it and does not change it.", "",
        "## Criteria", "", "| Id | Type | Weight |", "|---|---|---:|",
        *[f"| {c.id} | {c.type} | {c.weight} |" for c in sc.criteria], "",
        *[f"- **{c.id}** ({c.type}): {_rule(c)}" for c in sc.criteria], "",
        "## When a criterion does not apply", "",
        "- A preference you did not set is not scored, and its weight goes to the other criteria. The report shows it as n/a.",
        "- A fact the offer does not state never counts as a plus: it earns no points (a line with two checks, such as location, still earns the part the offer states), and the line descriptions above say when it is also listed under unknowns. The skills and language lines are the exception: they do not apply when the offer names no skill or language honestcv knows.", "",
        "## Layers", "",
        "The layer is the direction of a role relative to your profile. It is not a grade and not a veto.", "",
        "- The offer level is the one the source states, else a level word in the title, else the years of experience the offer asks for: "
        + ", ".join(f"{lvl} from {lo}" for lo, lvl in levels["years_bands"]) + ".",
        "- STRETCH when the offer level is above profile.level, or the offer asks for 3 or more years above your years. Checked first.",
        "- DOWNSHIFT when the offer level is below profile.level. Otherwise CORE.",
        "- Without profile.level, or when the offer states no level, the layer is CORE and marked as assumed.", "",
        "## Filtered out before scoring", "",
        "An offer that hits a gate gets no percentage. It is listed under Filtered out with the reason and a quote. Gates run in this order, and the first hit is shown.", "",
        "1. `deny_title` and `deny_term`: your deny lists.",
        "2. `commission_only`, `unpaid_task`, `equity_only`: the phrases below, unless a negation such as not or no stands just before.",
        "3. `expired`: valid_through is before today.",
        f"4. `stale`: posted more than {cfg.preferences.freshness_days} days ago.",
        "5. `no_content`: no description, no skills and no link.",
        "6. `ledger_ceiling`: a required sentence matches a limit your ledger states. Sentences marked nice to have do not count.", "",
        *[f"- `{code}`: {', '.join(gates[code])}" for code in ("commission_only", "unpaid_task", "equity_only")], "",
        "## Penalties", "",
        *[
            f"- `{p.code}`: "
            + (
                "the gap is the years the offer asks for minus your years. A gap "
                + "; ".join(f"of {s.min_gap:g} or more costs {abs(s.points)} points" for s in sorted(p.steps, key=lambda s: s.min_gap))
                + ". The largest matching step applies. Not applied when either side is unknown."
                if p.code == "years_gap"
                else f"{abs(p.points or 0)} points, once, when a required skill you lack is mentioned within 120 characters of a hard word such as required, expert or advanced. Nice-to-have skills never cost points."
            )
            for p in sc.penalties
        ], "",
        "## Which offers are shown", "",
        f"An offer is shown when it is not filtered out, not low confidence, and its match reaches the minimum of its layer: {surface}. There is no daily cap.",
        f"Bands only set the colour: {sc.bands.strong} and above is strong, {sc.bands.good} and above is good, anything lower that is shown is fair. They never decide what is shown.", "",
        "An offer is low confidence when any of these holds. It is then listed under Needs the full text and never ranked.", "",
        f"- The criteria that apply carry less than {sc.min_applicable_weight} of the total weight.",
        "- The offer came as a title and a link only.",
        "- Fewer than 3 requirements were recognized.", "",
        "## Order", "",
        "Shown offers are sorted by match, highest first. Ties go to the newer posting, then to the offer id, then to the CV file name. The order never depends on the order of the input.", "",
    ]  # fmt: skip
    return "\n".join(lines)
