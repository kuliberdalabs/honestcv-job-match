"""Gates, layer, criteria, penalties, surface rule and ranking.

Everything person-specific (weights, thresholds, word lists) comes from honestcv.yml and the data packs.
"""

import datetime as dt
import functools
import math
import re
from collections.abc import Sequence
from typing import NamedTuple

from honestcv.config import Config, Criterion
from honestcv.models import (CriterionScore, Gate, Layer, LedgerIndex, Level, MatchResult, Offer, Penalty, Preferences,
                             Profile, Requirement, Salary)  # fmt: skip
from honestcv.profile import canonical, known_skill, pack, scan_skills, skill_tokens
from honestcv.textutil import NUMBER_WORDS, _token_regex, hit, snippet, stem, tokenize

__all__ = ["assign_layer", "best_per_offer", "check_gates", "extract_requirements", "monthly_salary", "offer_level",
           "required_years", "score_all", "score_offer", "soft_spans"]  # fmt: skip

RANK = {"intern": 0, "junior": 1, "mid": 2, "senior": 3, "lead": 4}
_BULLET = re.compile(r"^\s*(?:[-*+\u2022\u25e6\u25aa\u2023]|\d{1,3}[.)])\s+")
_NEGATION = re.compile(r"(?<!\w)(?:not|no|isn't|without|nie|bez)(?!\w)")


def _spans(low: str, token: str) -> list[tuple[int, int]]:
    return [m.span() for m in _token_regex(token).finditer(low)]


def _inside(spans: Sequence[tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in spans)


def _phrases(words: Sequence[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


# ---------------------------------------------------------------- soft windows, requirement headings


@functools.cache
def _soft_rx() -> re.Pattern:
    return re.compile(rf"(?<!\w)(?:{_phrases(pack('skills.yml')['soft_words'])})(?!\w)", re.IGNORECASE)


@functools.cache
def _heading_rx() -> tuple[re.Pattern, re.Pattern]:
    """(a line that starts with a requirements heading, an inline "Requirements:" label)."""
    heads = _phrases(pack("levels.yml")["requirement_headings"])
    return re.compile(rf"^\W*(?:{heads})(?!\w)", re.IGNORECASE), re.compile(rf"(?<!\w)(?:{heads})\s*:", re.IGNORECASE)


@functools.lru_cache(maxsize=256)
def soft_spans(low: str) -> tuple[tuple[int, int], ...]:
    """Character ranges of lower-cased offer text that hold only optional requirements.

    A window starts at a soft word ("nice to have", "a plus", "mile widziane") and ends at the end of the sentence,
    the end of the line, or the next requirements label, whichever comes first. A soft heading on its own line
    ("Nice to have") makes the lines under it soft until a requirements heading, or a blank line followed by prose.
    """
    soft, (head_rx, label_rx) = _soft_rx(), _heading_rx()
    spans: list[tuple[int, int]] = []
    section = blank = False
    pos = 0
    for line in low.split("\n"):
        start, pos = pos, pos + len(line) + 1
        s = line.strip()
        if not s:
            blank = True
            continue
        bullet = bool(_BULLET.match(line))
        section = section and not (blank and not bullet)
        blank = False
        heading = not bullet and len(s.split()) <= 6 and "," not in s and (s.endswith(":") or ":" not in s) and s[-1] not in ".!?"
        if not bullet and head_rx.match(s):
            section = False
            if heading:
                continue
        section = section or bool(heading and soft.match(s))
        if section:
            spans.append((start, start + len(line)))
            continue
        for m in soft.finditer(line):
            reset, stop = label_rx.search(line, m.end()), line.find(". ", m.end())
            ends = [len(line)] + ([reset.start()] if reset else []) + ([stop + 1] if stop >= 0 else [])
            spans.append((start + m.start(), start + min(ends)))
    return tuple(spans)


# ---------------------------------------------------------------- required years, level, layer

_NUM = "|".join(sorted((w for w, v in NUMBER_WORDS.items() if 1 <= v <= 10), key=len, reverse=True))
_N = rf"(?P<n>\d{{1,2}}|{_NUM})"
_UNIT = r"(?P<u>years?|yrs?|lat|lata|rok|roku)(?!\w)"
_YEAR_RES = (
    re.compile(rf"(?<!\w)(?:minimum(?:\s+of)?|min\.?|at\s+least|co\s+najmniej)\s*(?:{_N}\s*\+?\s*)?{_UNIT}"),
    re.compile(rf"(?<!\w){_N}\s*(?:\+|or\s+more)\s*{_UNIT}"),
    re.compile(rf"(?<!\w){_N}\s*{_UNIT}['\u2019]?\s+(?:of\s+)?(?:[a-ząćęłńóśźż-]+\s+){{0,2}}?(?:experience|doświadczeni\w*)"),
    re.compile(rf"(?<!\w)od\s+{_N}\s+(?P<u>lat)(?!\w)"),
)  # fmt: skip
_RANGE = re.compile(rf"(?<![\d.]){_N}\s*[-\u2013]\s*\d{{1,2}}(?=\s*{_UNIT})")  # "3-5 years" asks for 3
_YOU = re.compile(r"\b(?:you|your|masz|posiadasz|wymag\w*)\b", re.IGNORECASE)


def _years_scope(text: str) -> str:
    """The text after the first requirements heading; without one, bullet lines and sentences that address the reader."""
    lines, head_rx = text.split("\n"), _heading_rx()[0]
    for i, line in enumerate(lines):
        if not _BULLET.match(line) and head_rx.match(line.strip().lower()):
            return "\n".join(lines[i:])
    keep: list[str] = []
    for line in lines:
        keep += [line] if _BULLET.match(line) else [p for p in re.split(r"(?<=[.!?])\s+", line) if _YOU.search(p)]
    return "\n".join(keep)


def _years_hit(text: str) -> tuple[int, str] | None:
    scope = _years_scope(text)
    low = scope.lower()
    # blank out the upper bound of a range, so "3-5 years of experience" is read as 3 and not also as 5
    masked = _RANGE.sub(lambda m: low[m.start() : m.end("n")] + " " * (m.end() - m.end("n")), low)
    best = None
    for rx, source in [(_RANGE, low), *((rx, masked) for rx in _YEAR_RES)]:
        for m in rx.finditer(source):
            n = m.groupdict().get("n")
            if n is None and m.group("u") not in ("rok", "roku", "year", "yr"):
                continue
            value = 1 if n is None else int(n) if n.isdigit() else NUMBER_WORDS[n]
            if value <= 15 and (best is None or value > best[0]):
                best = (value, snippet(scope, m.start(), m.end()))
    return best


def required_years(text: str) -> int | None:
    """The most years of experience the text asks for (values above 15 are discarded), or None."""
    found = _years_hit(text)
    return found[0] if found else None


def offer_level(offer: Offer) -> Level | None:
    """The source's level, else a level word in the title, else the band of the required years."""
    if offer.level:
        return offer.level
    cfg = pack("levels.yml")
    title = " " + " ".join(offer.title.lower().split()) + " "
    for phrase in cfg["exclude"]:
        title = title.replace(phrase, " ")
    found = [lvl for lvl, words in cfg["levels"].items() if any(hit(title, w) for w in words)]
    if found:
        return max(found, key=RANK.__getitem__)  # type: ignore[return-value]
    years = required_years(offer.description)
    return None if years is None else next(lvl for lo, lvl in reversed(cfg["years_bands"]) if years >= lo)


def assign_layer(offer: Offer, profile: Profile) -> tuple[Layer, bool]:
    """(layer, assumed). STRETCH is decided before DOWNSHIFT. Without profile.level, or when the offer states
    no level, the layer is CORE and assumed."""
    level = offer_level(offer)
    if profile.level is None or level is None:
        return "CORE", True
    years = required_years(offer.description)
    if RANK[level] > RANK[profile.level] or (years is not None and profile.years is not None and years - profile.years >= 3):
        return "STRETCH", False
    return ("DOWNSHIFT" if RANK[level] < RANK[profile.level] else "CORE"), False


# ---------------------------------------------------------------- gates


def check_gates(offer: Offer, prefs: Preferences, index: LedgerIndex | None, today: dt.date) -> Gate | None:
    """The first gate that applies, in the order docs/scoring.md lists them, or None. A gate has a reason and a quote."""
    whole = offer.title + "\n" + offer.description
    low = whole.lower()
    for token in prefs.deny_titles:
        if hit(offer.title.lower(), token):
            return Gate(code="deny_title", detail=f'the title matches your deny_titles entry "{token}"', quote=offer.title[:120])
    for token in prefs.deny_terms:
        for s, e in _spans(low, token)[:1]:
            return Gate(code="deny_term", detail=f'the text matches your deny_terms entry "{token}"', quote=snippet(whole, s, e))
    phrases = pack("gates.yml")
    for code in ("commission_only", "unpaid_task", "equity_only"):
        for phrase in phrases[code]:
            for s, e in _spans(low, phrase):
                if not _NEGATION.search(low[max(0, s - 20) : s]):
                    return Gate(code=code, detail=f'the text says "{phrase}"', quote=snippet(whole, s, e))  # type: ignore[arg-type]
    if offer.valid_through and offer.valid_through < today:
        return Gate(code="expired", detail=f"the offer closed on {offer.valid_through.isoformat()}")
    if offer.posted_at and (today - offer.posted_at).days > prefs.freshness_days:
        days = (today - offer.posted_at).days
        return Gate(code="stale", detail=f"posted {days} days ago; freshness_days is {prefs.freshness_days}")
    if not (offer.description.strip() or offer.skills or offer.skills_nice or offer.url):
        return Gate(code="no_content", detail="the offer has no description, no skills and no link")
    if index:
        soft = soft_spans(offer.description.lower())
        for claim_id, pattern in index.ceiling_patterns:
            try:
                rx = re.compile(pattern, re.IGNORECASE)
            except re.error:
                continue
            for m in rx.finditer(offer.description):
                if not _inside(soft, m.start()):
                    return Gate(code="ledger_ceiling", detail=f"the offer requires what your ledger rules out (claim {claim_id})",
                                quote=snippet(offer.description, m.start(), m.end()))  # fmt: skip
    return None


# ---------------------------------------------------------------- requirements


def _requirements(offer: Offer, profile: Profile, index: LedgerIndex | None) -> tuple[list[Requirement], dict[str, list[tuple[int, int]]]]:
    text, mine = offer.description, set(profile.skills)
    low = text.lower()
    spans: dict[str, list[tuple[int, int]]] = {}
    must: dict[str, bool] = {}
    if offer.skills or offer.skills_nice:  # structured lists are the whole requirement set
        for term, is_must in [(canonical(t), False) for t in offer.skills_nice] + [(canonical(t), True) for t in offer.skills]:
            must[term] = is_must or must.get(term, False)
        for term in must:
            spans[term] = sorted({sp for tok in skill_tokens(term) for sp in _spans(low, tok)})
    else:
        soft = soft_spans(low)
        extra = [(s, a, b) for s in sorted(mine) if not known_skill(s) for a, b in _spans(low, s)]  # skills_add entries
        for term, a, b in sorted([*scan_skills(low), *extra], key=lambda h: h[1]):
            must[term] = must.get(term, False) or not _inside(soft, a)
            spans.setdefault(term, []).append((a, b))
    reqs = []
    for term, is_must in must.items():
        have = term in mine
        backed = sorted(index.skill_backing.get(term, [])) if index and have else []
        quote = snippet(text, *spans[term][0]) if spans.get(term) else None
        reqs.append(Requirement(term=term, must=is_must, in_profile=have, backed_by=backed, quote=quote))
    return reqs, spans


def extract_requirements(offer: Offer, profile: Profile, index: LedgerIndex | None) -> list[Requirement]:
    """What the offer asks for: its structured skill lists if it has any, else every known skill in its text.
    Optional ones (a soft word before them) get must=False. Each carries the ledger claims that back it."""
    return _requirements(offer, profile, index)[0]


# ---------------------------------------------------------------- criteria


class _R(NamedTuple):
    applicable: bool = True
    f: float = 0.0
    note: str = ""
    quotes: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


class _Ctx(NamedTuple):
    offer: Offer
    profile: Profile
    prefs: Preferences
    layer: Layer
    reqs: list[Requirement]


def _na(why: str = "no preference set") -> _R:
    return _R(False, 0.0, f"n/a: {why}, weight redistributed")


def _skills_c(c: Criterion, x: _Ctx) -> _R:
    must, nice = [r for r in x.reqs if r.must], [r for r in x.reqs if not r.must]
    denom = len(must) + c.nice_to_have_credit * len(nice)
    if not x.reqs or denom == 0:
        return _na("the offer lists no skills honestcv knows")
    got_must, got_nice = sum(r.in_profile for r in must), sum(r.in_profile for r in nice)
    missing = [r for r in must + nice if not r.in_profile]
    note = f"{got_must} of {len(must)} required skills" + (f", {got_nice} of {len(nice)} nice-to-have" if nice else "") + "."
    note += f" Missing: {', '.join(r.term for r in missing)}." if missing else ""
    quotes = tuple(r.quote for r in missing + [r for r in x.reqs if r.in_profile] if r.quote)[:3]
    return _R(True, (got_must + c.nice_to_have_credit * got_nice) / denom, note, quotes)


@functools.lru_cache(maxsize=4096)
def _is_level_word(token: str) -> bool:
    return any(hit(token, w) for words in pack("levels.yml")["levels"].values() for w in words)


def _title_tokens(text: str) -> frozenset[str]:
    return frozenset(stem(t) for t in tokenize(text) if not _is_level_word(t))


def _title_c(c: Criterion, x: _Ctx) -> _R:
    targets = [t for t in (x.profile.target_titles or x.profile.titles) if _title_tokens(t)]
    if not targets:
        return _na("your profile has no job titles")
    generic, mine = {stem(t) for t in pack("levels.yml")["generic_title_tokens"]}, _title_tokens(x.offer.title)
    best = (-1.0, 0, "")
    for target in targets:
        words = _title_tokens(target)
        shared = mine & words
        m = len(shared) if shared - generic else 0
        f = 1.0 if m >= min(c.threshold or 2, len(words)) else 0.5 if m >= 1 else 0.0
        best = max(best, (f, m, target), key=lambda b: (b[0], b[1]))
    f, m, target = best
    note = f'{m} shared word(s) with your title "{target}"' if m else "no word in common with your target titles"
    return _R(True, f, note, (x.offer.title[:120],))


def _level_c(c: Criterion, x: _Ctx) -> _R:
    if x.profile.level is None:
        return _na("profile.level is not set")
    level, found = offer_level(x.offer), _years_hit(x.offer.description)
    if level is None:
        return _R(True, 0.0, "the offer states no level, so CORE is assumed", (), ("level not stated, assumed CORE",))
    quote = (found[1] if found and not x.offer.level else x.offer.title)[:120]
    default = 1.0 if x.layer == "CORE" else 0.5
    return _R(True, c.points.get(x.layer, default), f"{level} role for a {x.profile.level} profile: {x.layer}", (quote,))


def _location_c(c: Criterion, x: _Ctx) -> _R:
    p, o = x.prefs, x.offer
    if not (p.remote or p.countries or p.cities):
        return _na()
    where = (o.location or "").lower()
    place_known = bool(o.countries or where.strip())
    if o.remote is None and not place_known:
        return _R(True, 0.0, "the offer states no work mode or location", (), ("work mode and location not stated",))
    mode_ok = not p.remote or o.remote in p.remote
    wants_place = bool(p.countries or p.cities)
    open_world = any(hit(where, w) for w in ("anywhere", "worldwide", "global"))
    place_ok = not wants_place or open_world or bool({k.upper() for k in o.countries} & set(p.countries)) or any(hit(where, k.lower()) for k in p.cities)
    unknown: list[str] = []
    if mode_ok and place_ok:
        f = 1.0
    elif mode_ok and wants_place and not place_known and o.remote == "remote":
        f, unknown = 0.5, ["remote, country rule not stated"]
    else:
        f = 0.5 if (mode_ok or place_ok) else 0.0
        unknown = ["work mode not stated"] if o.remote is None and p.remote else []
    note = f"work mode {o.remote or 'not stated'}; place {'matches' if place_ok else 'does not match'}"
    return _R(True, f, note, ((o.location or "")[:120],) if o.location else (), tuple(unknown))


_PER_MONTH = {"hour": 160, "day": 20, "month": 1, "year": 1 / 12}
_HARD_CURRENCIES = {"USD", "EUR", "GBP", "CHF"}


def _salary_unit(s: Salary, value: float) -> str | None:
    """The stated unit, else the unit implied by the amount, else None when it cannot be told."""
    if s.unit:
        return s.unit
    cur = (s.currency or "").upper()
    if value < 500:
        return "hour"
    if cur in _HARD_CURRENCIES:
        return None if value <= 2500 else "year" if value >= 20000 else "month"
    return None if cur == "PLN" and value <= 4000 else "month"


def monthly_salary(s: Salary, currency: str) -> float | None:
    """The offer's highest stated amount per month in that currency (hour x 160, day x 20, year / 12), or None
    when the currency differs, no amount is stated or the unit cannot be told."""
    value = s.max if s.max is not None else s.min
    if value is None or (s.currency or "").upper() != currency.upper():
        return None
    unit = _salary_unit(s, value)
    return None if unit is None else value * _PER_MONTH[unit]


def _salary_c(c: Criterion, x: _Ctx) -> _R:
    s = x.offer.salary
    if not x.prefs.min_salary:
        return _na()
    if s is None or (s.min is None and s.max is None):
        return _R(True, 0.0, "the offer states no salary", (), ("salary not stated",))
    cur = (s.currency or "").upper()
    if not cur:
        return _R(True, 0.0, "the offer states no currency", (), ("salary currency not stated",))
    want = next((m for m in x.prefs.min_salary if m.currency.upper() == cur), None)
    if want is None:
        return _R(False, 0.0, f"n/a: you set no {cur} minimum, weight redistributed", (), (f"salary in {cur}; you set no {cur} minimum",))
    got = monthly_salary(s, cur)
    if got is None:
        return _R(True, 0.0, "the salary unit is not stated", (), ("salary unit not stated",))
    need = want.amount * _PER_MONTH[want.unit]
    unit = _salary_unit(s, s.max if s.max is not None else s.min or 0)
    unknown = (f"salary unit assumed: {unit}",) if s.unit is None or s.unit_assumed else ()
    f = 1.0 if got >= need else 0.5 if got >= c.near_ratio * need else 0.0
    return _R(True, f, f"{got:,.0f} {cur} per month against your minimum of {need:,.0f}", (f"{s.max or s.min:,.0f} {cur} per {unit}",), unknown)


def _contract_c(c: Criterion, x: _Ctx) -> _R:
    if not x.prefs.contract:
        return _na()
    if not x.offer.contract:
        return _R(True, 0.0, "the offer states no contract form", (), ("contract form not stated",))
    return _R(True, 1.0 if set(x.offer.contract) & set(x.prefs.contract) else 0.0, "offer: " + ", ".join(sorted(x.offer.contract)))


def _offer_languages(offer: Offer) -> tuple[list[str], list[str]]:
    """(codes, quotes): structured languages plus names in the text within 60 characters of a requirement cue."""
    cfg, low = pack("languages.yml"), offer.description.lower()
    codes, quotes = {k.lower() for k in offer.languages}, []
    for code, names in cfg["names"].items():
        for name in names:
            for s, e in _spans(low, name):
                follow = re.match(r"\s+(\w+)", low[e:])
                if (follow and follow.group(1) in cfg["determiners"]) or not any(hit(low[max(0, s - 60) : e + 60], cue) for cue in cfg["cues"]):
                    continue
                codes.add(code)
                quotes.append(snippet(offer.description, s, e))
    return sorted(codes), quotes[:2]


def _language_c(c: Criterion, x: _Ctx) -> _R:
    codes, quotes = _offer_languages(x.offer)
    if not codes:
        return _na("the offer names no required language")
    label = {k: v[0].capitalize() for k, v in pack("languages.yml")["names"].items()}
    lacking = [label.get(k, k) for k in codes if k not in x.profile.languages]
    note = "requires " + ", ".join(label.get(k, k) for k in codes) + (f"; not in your CV: {', '.join(lacking)}" if lacking else "")
    return _R(True, 0.0 if lacking else 1.0, note, tuple(quotes))


def _tokens_c(c: Criterion, x: _Ctx) -> _R:
    whole = x.offer.title + "\n" + x.offer.description
    found = [(t, _spans(whole.lower(), t)) for t in c.tokens]
    found = [(t, sp) for t, sp in found if sp]
    f = 1.0 if len(found) >= (c.threshold or 1) else 0.5 if found else 0.0
    return _R(True, f, f"{len(found)} of {len(c.tokens)} tokens found", tuple(snippet(whole, *sp[0]) for _, sp in found)[:3])


_EVAL = {"skills_coverage": _skills_c, "title_match": _title_c, "level": _level_c, "location": _location_c,
         "salary": _salary_c, "contract": _contract_c, "language": _language_c, "tokens": _tokens_c}  # fmt: skip


# ---------------------------------------------------------------- scoring


def _half_up(x: float) -> int:
    return math.floor(round(x, 6) + 0.5)


def _hard_missing(reqs: list[Requirement], spans: dict[str, list[tuple[int, int]]], text: str) -> str | None:
    """A quote when a must-have that the profile lacks sits within 120 characters of a hard word ("required")."""
    low, hard = text.lower(), pack("skills.yml")["hard_words"]
    for r in reqs:
        for s, e in spans.get(r.term, ()) if r.must and not r.in_profile else ():
            if any(hit(low[max(0, s - 120) : e + 120], w) for w in hard):
                return snippet(text, s, e)
    return None


def score_offer(offer: Offer, profile: Profile, cfg: Config, index: LedgerIndex | None, today: dt.date) -> MatchResult:
    """One offer against one CV: gate, layer, criteria, penalties, match %, surface and confidence."""
    sc = cfg.scoring
    gate = check_gates(offer, cfg.preferences, index, today)
    layer, assumed = assign_layer(offer, profile)
    if gate:
        return MatchResult(offer_id=offer.id, cv_id=profile.cv_id, gate=gate, layer=layer, layer_assumed=assumed, criteria=[], penalties=[],
                           base=0.0, match_pct=0, unknowns=[], requirements=[], requirements_recognized=0, backed=(0, 0),
                           surfaced=False, low_confidence=False, band=None)  # fmt: skip
    reqs, spans = _requirements(offer, profile, index)
    x = _Ctx(offer, profile, cfg.preferences, layer, reqs)
    scored = [(c, _EVAL[c.type](c, x)) for c in sc.criteria]
    applicable = sum(c.weight for c, r in scored if r.applicable)
    share = (lambda c, r: 100 * c.weight * r.f / applicable if r.applicable and applicable else 0.0)
    criteria = [CriterionScore(id=c.id, weight=c.weight, applicable=r.applicable, fraction=round(r.f, 4) if r.applicable else 0.0,
                               points=round(share(c, r), 4), note=r.note, quotes=[q[:120] for q in r.quotes])
                for c, r in scored]  # fmt: skip
    base = sum(share(c, r) for c, r in scored)
    penalties: list[Penalty] = []
    for rule in sc.penalties:
        if rule.code == "years_gap":
            found = _years_hit(offer.description)
            steps = [st.points for st in sorted(rule.steps, key=lambda st: st.min_gap) if found and profile.years is not None and found[0] - profile.years >= st.min_gap]
            if steps and steps[-1]:
                penalties.append(Penalty(code="years_gap", points=steps[-1], quote=found[1]))  # type: ignore[index]
        elif rule.code == "hard_requirement_missing" and rule.points:
            quote = _hard_missing(reqs, spans, offer.description)
            if quote:
                penalties.append(Penalty(code="hard_requirement_missing", points=rule.points, quote=quote))
    pct = max(0, min(100, _half_up(base + sum(p.points for p in penalties))))
    low = applicable == 0 or applicable < sc.min_applicable_weight or offer.thin or len(reqs) < 3
    mine = [r for r in reqs if r.in_profile]
    return MatchResult(
        offer_id=offer.id, cv_id=profile.cv_id, gate=None, layer=layer, layer_assumed=assumed, criteria=criteria, penalties=penalties,
        base=round(base, 4), match_pct=pct, unknowns=[u for _, r in scored for u in r.unknown], requirements=reqs,
        requirements_recognized=len(reqs), backed=(sum(bool(r.backed_by) for r in mine), len(mine)),
        surfaced=not low and pct >= sc.surface.min_match(layer), low_confidence=low,
        band=None if low else "strong" if pct >= sc.bands.strong else "good" if pct >= sc.bands.good else "fair",
    )  # fmt: skip


def score_all(offers: list[Offer], profiles: list[Profile], cfg: Config, index: LedgerIndex | None, today: dt.date) -> list[MatchResult]:
    """Every offer against every CV, in offer id then cv id order, so the result never depends on input order."""
    return [score_offer(o, p, cfg, index, today) for o in sorted(offers, key=lambda o: o.id) for p in sorted(profiles, key=lambda p: p.cv_id)]


def best_per_offer(results: list[MatchResult], offers: Sequence[Offer] | None = None) -> list[MatchResult]:
    """Surfaced results only, the best CV per offer, ranked: match descending, then newer posted_at (pass offers
    to use it), then offer id, then cv id."""
    posted = {o.id: o.posted_at for o in offers or ()}
    best: dict[str, MatchResult] = {}
    for r in results:
        if r.surfaced and (r.offer_id not in best or (-r.match_pct, r.cv_id) < (-best[r.offer_id].match_pct, best[r.offer_id].cv_id)):
            best[r.offer_id] = r

    def key(r: MatchResult) -> tuple:
        day = posted.get(r.offer_id)
        return (-r.match_pct, -(day.toordinal() if day else 0), r.offer_id, r.cv_id)

    return sorted(best.values(), key=key)
