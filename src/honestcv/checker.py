"""Claims checker: read a document from disk, attribute each claim-bearing unit to a ledger claim,
run the guards and give a verdict. It only reports: the checked file is never written."""

import datetime as dt
import difflib
import functools
import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from honestcv.ledger import YamlLoader, lint_ledger
from honestcv.models import CheckFlag, CheckResult, Claim, DurationMention, Ledger, NumberMention, Offer, Requirement
from honestcv.textutil import NUMBER_WORDS, durations, home_relative, normalize, numbers, sentences, stem, stopwords

LADDER = {"built": 1, "tested": 2, "shipped": 3, "used": 4, "acknowledged": 5, "paid": 6}
_RANK_6C = {**LADDER, "generated": 0.5, "non_cash_value": 0.5}  # generated counts below built (guard 6c)
_MATRIX = {  # authorship.role -> verb class -> severity; a missing cell is ok
    "sole": {},
    "led": {"authored": "REVIEW"},
    "contributed": {"authored": "BLOCK", "led": "BLOCK", "directed": "REVIEW"},
    "directed": {"authored": "BLOCK", "led": "REVIEW", "contributed": "REVIEW"},
}
_MARKER = re.compile(r"<!--\s*c:\s*(.*?)\s*-->")
_STRIP_MARKER = re.compile(r"[ \t]*<!--\s*c:.*?-->")
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
_CLAUSE_END = re.compile(r"[;:]|[.!?](?=\s|$)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)(?:\s+#+)?$")
_LIST_GAP = re.compile(r"(?:[\s,;|/\u00b7]|\band\b|\bi\b)*", re.IGNORECASE)  # what may stand between two skills
# the job an opener names ("for the Junior Data Analyst position", "na stanowisko Młodszego Analityka"). Inside it the
# level words and skill names describe the job, not the person, so only those are set aside; every other word is checked
_ROLE_REF = re.compile(
    r"\s*(?:(?:for|to|in)\s+(?:the|this|your|a|an)\s+(?:[^\s,.;:!?]+\s+){0,7}?(?:position|role|job|opening|vacancy|post)\b"
    r"|(?:na|w\s+sprawie)\s+(?:stanowisk\w*|rol\w*|ofert\w*)"
    r"(?:\s+(?!(?:i|oraz|a|ale|lecz|bo|ponieważ|gdyż|że|któr\w*|gdzie|jako|mając\w*|znam|mam|posiadam|jestem)(?!\w))[^\s,.;:!?]+){0,5})",
    re.IGNORECASE,
)
_LEVEL_WORDS = re.compile(
    r"(?<!\w)(?:intern|trainee|junior|mid(?:-level)?|regular|senior|lead|principal|staff|head\s+of|team\s+lead|młodsz\w*|starsz\w*|stażyst\w*)(?!\w)",
    re.IGNORECASE,
)
# non-claim units with an open end (a salutation and up to four words, the title tailor writes): neutral only when the
# open part has no signal once level words and skill names are set aside ("Hello I hold a doctorate" is a claim)
_OPEN_UNIT = re.compile(r"(?:dear|hello|hi|good morning|szanown\w*|dzień dobry|witam|highlights for)\b", re.IGNORECASE)
# a consent clause is neutral when nothing in it is a claim word or verb (its dates, article numbers and words such as
# "recruitment" are not claims); a clause joined after it in the first person ("and I hold a doctorate") is checked in full
_CONSENT = re.compile(r"wyrażam zgodę na przetwarzanie|(?:i )?(?:hereby )?(?:consent|agree) to the processing", re.IGNORECASE)
_JOINED_CLAIM = re.compile(r"(?<!\w)(?:and|but|while|i|oraz|ale)\s+(?=(?:i|i'm|i've|ja|mam|znam|jestem|posiadam|umiem|potrafię)(?!\w)|\w+(?:łem|łam)(?!\w))",
                           re.IGNORECASE)
# a job title never speaks in the first person: with one of these words nothing is set aside
_FIRST_PERSON = re.compile(r"(?<!\w)(?:i|i'm|i've|i'd|my|me|mine|ja|mój|moja|moje|mnie|mi|mam|znam|jestem|posiadam|umiem|potrafię)(?!\w)", re.IGNORECASE)


# ---------------------------------------------------------------- vocabulary


def _rx(entry: str) -> "re.Pattern[str]":
    """hit() syntax as a pattern: "word*" is a prefix at a word start, a bare entry a whole word or phrase,
    an entry that starts or ends with a non-alphanumeric character (".net", "c++") matches as a substring there."""
    text = " ".join(str(entry).lower().split())
    star, core = text.endswith("*"), text.rstrip("*")
    body = r"\s+".join(re.escape(w).replace(r"\-", r"[-\s]?") for w in core.split())
    lead = r"(?<!\w)" if core[:1].isalnum() else ""
    trail = r"(?!\w)" if core[-1:].isalnum() and not star else ""
    return re.compile(lead + body + trail, re.IGNORECASE)


def _data(name: str) -> object:
    return yaml.load(resources.files("honestcv").joinpath("data", name).read_text(encoding="utf-8"), Loader=YamlLoader)


def _skills_source() -> object:
    """data/skills.yml as parsed YAML, or None when it is missing."""
    path = resources.files("honestcv").joinpath("data", "skills.yml")
    return yaml.load(path.read_text(encoding="utf-8"), Loader=YamlLoader) if path.is_file() else None


def _skill_entries(data: object) -> list[tuple[str, list[str], bool]]:
    """(canonical, aliases, ambiguous) from the skills: list, whose items are names or {name, aliases, ambiguous}."""
    items = data.get("skills", []) if isinstance(data, dict) else data or []
    out = []
    for item in items:
        entry = item if isinstance(item, dict) else {"name": item}
        name, aliases = entry.get("name"), entry.get("aliases") or []
        if isinstance(name, str) and name.strip():
            aliases = [aliases] if isinstance(aliases, str) else aliases
            out.append((name.strip().lower(), [str(a).strip().lower() for a in aliases if str(a).strip()], bool(entry.get("ambiguous"))))
    return out


@dataclass(frozen=True)
class Vocab:
    states: dict[str, list["re.Pattern[str]"]]
    classes: dict[str, list["re.Pattern[str]"]]
    groups: dict[str, list["re.Pattern[str]"]]  # venue, outcome, scope, benef, pay, claim, language
    negations: list["re.Pattern[str]"]
    not_entities: frozenset[str]
    labels: "re.Pattern[str]"
    nonclaims: list["re.Pattern[str]"]
    phrases: list["re.Pattern[str]"]  # letter openers and closers: they neutralise only themselves
    skills: list[tuple[str, "re.Pattern[str]", bool]]  # canonical, pattern, counts outside a skills line
    canon: dict[str, str]  # lower-case name or alias -> canonical


@functools.lru_cache(maxsize=1)
def vocab() -> Vocab:
    """Word lists from data/verbs.yml and data/nonclaims.yml, skills from data/skills.yml when it exists."""
    verbs, nonclaims = _data("verbs.yml"), _data("nonclaims.yml")
    words = verbs["claim_words"]
    sources = {"venue": "venue", "outcome": "outcomes", "scope": "scope", "benef": "beneficiaries", "pay": "pay_words"}
    groups = {key: [_rx(e) for e in verbs[src]] for key, src in sources.items()}
    groups["claim"] = [_rx(e) for lst in words.values() for e in lst]
    groups["language"] = [_rx(e) for e in words["languages"] + words["cefr"]]
    skills, canon = [], {}
    for name, aliases, ambiguous in _skill_entries(_skills_source()):
        for alias in [name, *aliases]:
            canon.setdefault(alias, name)
            skills.append((name, _rx(alias), not (ambiguous and alias == name)))
    labels = "|".join(re.escape(x) for x in sorted(verbs["skills_labels"], key=len, reverse=True))
    return Vocab(
        states={s: [_rx(e) for e in lst] for s, lst in verbs["states"].items()},
        classes={c: [_rx(e) for e in lst] for c, lst in verbs["authorship"].items()},
        groups=groups,
        negations=[_rx(e) for e in verbs["negations"]],
        not_entities=frozenset(verbs["not_entities"]) | {w for w in words["languages"] + words["cefr"] if "*" not in w},
        labels=re.compile(rf"^(?:{labels})\b", re.IGNORECASE),
        nonclaims=[re.compile(p, re.IGNORECASE) for p in nonclaims["units"]],
        phrases=[re.compile(rf"(?:{p})(?!\w)", re.IGNORECASE) for p in nonclaims.get("phrases", [])],
        skills=skills,
        canon=canon,
    )


def canonical_skill(name: str) -> str:
    """A skill name in lower case, mapped to its canonical skills.yml name when it is a known alias."""
    low = " ".join(name.lower().split())
    return vocab().canon.get(low, low)


# ---------------------------------------------------------------- text helpers


def exact_form(text: str) -> str:
    """Normalization for exact matching: NFKC, straight quotes, lower case, collapsed spaces, no trailing punctuation."""
    return " ".join(normalize(text).lower().split()).rstrip(".,;:!?… ")


def _word(raw: str) -> str:
    w = raw.lower().replace("\u2019", "'")
    return (w[:-2] if w.endswith("'s") else w).replace("'", "")


def _tokens(text: str) -> list[str]:
    """Stems of the content words of text: stopwords, digits and number words removed."""
    stop, out = stopwords(), []
    for m in _WORD.finditer(normalize(text)):
        w = _word(m.group(0))
        if w and w not in stop and not w.isdigit() and w not in NUMBER_WORDS:
            out.append(stem(w))
    return out


def _longest(found: Iterable[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Non-overlapping (start, end, tag) matches in text order; where two overlap the longer one is kept."""
    kept: list[tuple[int, int, str]] = []
    for s, e, tag in sorted(set(found), key=lambda t: (t[0] - t[1], t[0], t[2])):
        if e > s and all(e <= a or s >= b for a, b, _ in kept):
            kept.append((s, e, tag))
    return sorted(kept)


def _spans(patterns: list["re.Pattern[str]"], text: str) -> list[tuple[int, int]]:
    return [(s, e) for s, e, _ in _longest((m.start(), m.end(), "") for p in patterns for m in p.finditer(text))]


def _compile(pattern: str) -> "re.Pattern[str] | None":
    try:
        return re.compile(pattern)
    except re.error:
        return None


def strip_markers(text: str) -> str:
    """Removes <!-- c:ID --> citation markers (and the spaces before them); other comments stay."""
    return _STRIP_MARKER.sub("", text)


def skill_terms(text: str, *, skills_line: bool = False) -> list[tuple[str, int, int]]:
    """skills.yml terms in text as (canonical, start, end). Outside a skills line an ambiguous term counts only
    through a qualified alias (golang, "rest api"). Where two terms overlap the longer one wins."""
    found = ((m.start(), m.end(), name) for name, rx, outside in vocab().skills if skills_line or outside for m in rx.finditer(text))
    return [(name, s, e) for s, e, name in _longest(found)]


def document_units(text: str, *, headings: bool = True) -> list[tuple[int, str]]:
    """The units of sentences(), plus one unit per Markdown heading outside a comment: a heading is checked
    like a sentence."""
    units = sentences(text)
    if not headings:
        return units
    in_comment = False
    for no, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        m = None if in_comment else _HEADING.match(raw.strip())
        if m and (heading := sentences("- " + m.group(1))):
            units.append((no, heading[0][1]))
        opens, closes = raw.rfind("<!--"), raw.rfind("-->")
        in_comment = opens > closes if opens != closes else in_comment
    return sorted(units, key=lambda u: u[0])


# ---------------------------------------------------------------- units


@dataclass
class _Unit:
    line: int
    text: str
    norm: str
    cites: list[str] | None
    words: list[tuple[int, int, str]]
    hits: dict[str, list[tuple[int, int]]]
    nums: list[NumberMention]  # numbers for guard 6c: no calendar years, nothing inside a duration
    years: list[NumberMention]  # calendar years outside durations (guard 6e)
    durs: list[DurationMention]
    all_nums: list[NumberMention]
    ends: list[int]  # clause ends: ; : and sentence ends

    def clause(self, pos: int) -> int:
        return sum(1 for e in self.ends if e < pos)

    def neg_window(self, pos: int, size: int = 4) -> bool:
        """A negation within `size` words before pos (step 3, plural beneficiary nouns)."""
        return any(_is_neg(w) for s, e, w in [x for x in self.words if x[1] <= pos][-size:])

    def neg_clause(self, pos: int) -> bool:
        """A negation earlier in the same clause (negation scope of guards 6a, 6b, 6d)."""
        c = self.clause(pos)
        return any(_is_neg(w) for s, e, w in self.words if e <= pos and self.clause(s) == c)

    def found(self, key: str) -> list[tuple[int, int, str]]:
        return [(s, e, self.text[s:e]) for s, e in self.hits.get(key, [])]

    def states(self, negation_scope: bool = True) -> list[tuple[str, int, str]]:
        out = [(k.split(":", 1)[1], s, t) for k in self.hits if k.startswith("state:") for s, e, t in self.found(k)]
        return sorted((x for x in out if not (negation_scope and self.neg_clause(x[1]))), key=lambda x: x[1])

    def full(self, negation_scope: bool = False) -> set[str]:
        stop = stopwords()
        return {stem(w) for s, e, w in self.words if w not in stop and not (negation_scope and self.neg_clause(s))}


_NT = frozenset("dont doesnt didnt isnt arent wasnt werent cant couldnt wont wouldnt shouldnt havent hasnt hadnt".split())


def _is_neg(word: str) -> bool:
    """A negation word; contractions arrive without the apostrophe ("didn't" is "didnt")."""
    return word in _NT or any(p.fullmatch(word) for p in vocab().negations)


@dataclass
class _Ctx:
    ledger: Ledger
    today: dt.date
    states: dict[str, list["re.Pattern[str]"]]
    pos: dict[str, int]
    stems: dict[str, frozenset[str]]
    private: frozenset[str]
    company: frozenset[str] = frozenset()
    lint: bool = False
    by_id: dict[str, Claim] = field(default_factory=dict)

    @classmethod
    def build(cls, ledger: Ledger, today: dt.date, offer: Offer | None = None, lint: bool = False) -> "_Ctx":
        states = {s: list(p) for s, p in vocab().states.items()}
        for state, extra in ledger.rules.verbs_extra.items():
            states.setdefault(state, []).extend(_rx(e) for e in extra)
        claims = ledger.claims
        texts = {c.id: [c.summary, c.statement, *c.allowed, *c.skills, *c.not_confirmed, *c.private_names] for c in claims}
        return cls(ledger=ledger, today=today, states=states, pos={c.id: i for i, c in enumerate(claims)},
                   stems={cid: frozenset(t for x in parts for t in _tokens(x)) for cid, parts in texts.items()},
                   private=frozenset(t for c in claims for n in c.private_names for t in _tokens(n)),
                   company=frozenset(_word(w) for w in _WORD.findall(offer.company or "")) if offer else frozenset(),
                   lint=lint, by_id={c.id: c for c in claims})  # fmt: skip


def _unit(line: int, raw: str, ctx: _Ctx) -> _Unit:
    marks = _MARKER.findall(raw)
    text = " ".join(strip_markers(raw).split())
    voc = vocab()
    hits = {f"state:{s}": _spans(p, text) for s, p in ctx.states.items()}
    hits |= {f"class:{c}": _spans(p, text) for c, p in voc.classes.items()}
    hits |= {k: _spans(p, text) for k, p in voc.groups.items()}
    durs, all_nums = durations(text), numbers(text)
    dspans = [(d.start, d.start + len(d.raw)) for d in durs]
    free = [n for n in all_nums if all(n.start + len(n.raw) <= a or n.start >= b for a, b in dspans)]
    return _Unit(line=line, text=text, norm=exact_form(text), hits=hits, durs=durs, all_nums=all_nums,
                 cites=[c.strip() for m in marks for c in m.split(",") if c.strip()] if marks else None,
                 words=[(m.start(), m.end(), _word(m.group(0))) for m in _WORD.finditer(text)],
                 nums=[n for n in free if not n.is_year], years=[n for n in free if n.is_year],
                 ends=[m.start() for m in _CLAUSE_END.finditer(text)])  # fmt: skip


def _weights(u: _Unit, ctx: _Ctx) -> dict[str, int]:
    """Support tokens of a unit, without verbs and state nouns, scope or outcome words, durations and numbers.
    Tokens of any claim's private_names weigh 2."""
    masked = [s for k, v in u.hits.items() if k.split(":")[0] in ("state", "class", "venue", "outcome", "scope") for s in v]
    masked += [(n.start, n.start + len(n.raw)) for n in u.all_nums] + [(d.start, d.start + len(d.raw)) for d in u.durs]
    stop, out = stopwords(), {}
    for s, e, w in u.words:
        if w in stop or w.isdigit() or w in NUMBER_WORDS or any(a < e and s < b for a, b in masked):
            continue
        t = stem(w)
        out[t] = 2 if t in ctx.private else 1
    return out


def _support(weights: dict[str, int], stems: frozenset[str]) -> tuple[float, int]:
    total = sum(weights.values())
    shared = [t for t in weights if t in stems]
    return (sum(weights[t] for t in shared) / total if total else 0.0), len(shared)


def _overlap(item: str, full: set[str]) -> float:
    """Share of the item's tokens (numbers removed) found in the unit's token set."""
    toks = set(_tokens(item))
    return len(toks & full) / len(toks) if toks else 0.0


def _nonclaim(u: _Unit, ctx: "_Ctx") -> bool:
    """A unit on the non-claim list, or a letter opener or closer whose rest (after the job it names) has no claim signal."""
    voc = vocab()
    if any(p.fullmatch(u.norm) for p in voc.nonclaims):
        if _CONSENT.match(u.norm):
            joined = _JOINED_CLAIM.search(u.norm)
            head, tail = (u.norm[: joined.start()], u.norm[joined.end():]) if joined else (u.norm, "")
            return not _signal(_unit(u.line, head, ctx), numbers=False, skills=False) and not (tail and _signal(_unit(u.line, tail, ctx)))
        if not _OPEN_UNIT.match(u.norm):
            return True
        title = u.norm.casefold().startswith("highlights for")  # the title tailor writes: the offer's job and company
        return not _signal(_unit(u.line, _without_job_words(u.norm) if title else _LEVEL_WORDS.sub(" ", u.norm), ctx))
    m = next((m for p in voc.phrases if (m := p.match(u.norm))), None)
    if m is None or u.cites is not None:
        return False
    rest = u.norm[m.end():]
    if role := _ROLE_REF.match(rest):
        rest = _without_job_words(rest[:role.end()]) + rest[role.end():]
    return not _signal(_unit(u.line, rest, ctx))


def _without_job_words(text: str) -> str:
    """text without level words and skill names, the words a job title is made of; unchanged when it speaks in the
    first person, which a job title never does."""
    if _FIRST_PERSON.search(text):
        return text
    for _, s, e in reversed(skill_terms(text, skills_line=True)):
        text = text[:s] + " " + text[e:]
    return _LEVEL_WORDS.sub(" ", text)


def _signal(u: _Unit, *, numbers: bool = True, skills: bool = True) -> bool:
    """A claim signal: a state, authorship, venue or outcome word, a claim word, a number, a duration, a skills
    term or a citation marker. A word shared with a claim's summary is not one."""
    if u.cites is not None or any(v for k, v in u.hits.items() if k.split(":")[0] in ("state", "class", "venue", "outcome", "claim")):
        return True
    return bool((numbers and (u.all_nums or u.durs)) or (skills and skill_terms(u.text, skills_line=bool(_labelled(u)))))


def _matched(path: str, k: Claim | None) -> bool:
    """Whether attribution found a claim for a unit without a signal. Such a unit is never UNBACKED, and a
    draft found only on the ambiguous path does not count: a draft is not ledger backing."""
    return path != "none" and not (path == "ambiguous" and k is not None and k.draft)


def _claim_bearing(u: _Unit, ctx: _Ctx) -> bool:
    """Not on the non-claim list, and a claim signal or (without one) a claim the unit is attributed to."""
    if _nonclaim(u, ctx):
        return False
    return _signal(u) or _matched(*_attribute(u, ctx, None)[:2])


def _skill_list(text: str) -> int:
    """How many skills.yml terms text lists when it is a pure list: each item one term or alias, items separated
    by , ; | / \u00b7 "and" or "i", nothing else; 0 when anything else stands there (a number, a word, a bracket)."""
    body = text.rstrip(" .,;")
    terms = skill_terms(body, skills_line=True)
    if not terms or body[: terms[0][1]].strip() or body[terms[-1][2] :].strip():
        return 0
    gaps = [body[a[2] : b[1]] for a, b in zip(terms, terms[1:])]
    return len(terms) if all(g.strip() and _LIST_GAP.fullmatch(g) for g in gaps) else 0


def _labelled(u: _Unit) -> "re.Match[str] | None":
    return vocab().labels.match(u.text)


def _is_skills_line(u: _Unit) -> bool:
    """A pure list of skills.yml terms after a skills label, or of three or more without one. A bare "Skills"
    title, or a list with anything else on it, is not one: such a unit goes through the claim guards."""
    label = _labelled(u)
    if label:
        return _skill_list(u.text[label.end() :].lstrip(" :-\u2013\u2014")) >= 1
    return _skill_list(u.text) >= 3


def _plain_ctx() -> _Ctx:
    return _Ctx.build(Ledger(schema_version=1, person={}), dt.date.today())


def is_skills_line(text: str) -> bool:
    return _is_skills_line(_unit(0, text, _plain_ctx()))


def claim_units(text: str, ledger: Ledger) -> list[tuple[int, str]]:
    """The units of a document the check reads as claims against ledger: skills lines and claim-bearing units."""
    ctx = _Ctx.build(ledger, dt.date.today())
    return [(no, t) for no, t in document_units(text) if _is_skills_line(u := _unit(no, t, ctx)) or _claim_bearing(u, ctx)]


# ---------------------------------------------------------------- guards

_Hit = tuple[str, str, Claim | None, str | None, str]  # rule, severity, claim, field path, message


def _doc_guards(u: _Unit, ctx: _Ctx) -> tuple[list[_Hit], Claim | None]:
    """Step 3, on every unit: forbidden phrases, ceilings, private names, visibility: never claims."""
    out: list[_Hit] = []
    negated_ceiling = None
    for j, fp in enumerate(ctx.ledger.rules.forbidden_phrases):
        m = (rx := _compile(fp.pattern)) and rx.search(u.text)
        if m:
            why = f" ({fp.reason})" if fp.reason else ""
            out.append(("FORBIDDEN_PHRASE", "BLOCK", None, f"rules.forbidden_phrases[{j}]", f'"{m.group(0)}" matches rules.forbidden_phrases[{j}]{why}'))
    for i, c in enumerate(ctx.ledger.claims):
        if c.kind == "ceiling" and u.norm not in {exact_form(s) for s in c.allowed}:
            for j, pattern in enumerate(c.forbidden_here):
                m = (rx := _compile(pattern)) and rx.search(u.text)
                if m:
                    neg = u.neg_window(m.start())
                    negated_ceiling = negated_ceiling or (c if neg else None)
                    tail = " after a negation" if neg else ""
                    message = f'{c.id}.forbidden_here[{j}] matches "{m.group(0)}"{tail}'
                    out.append(("CEILING_BREACH", "REVIEW" if neg else "BLOCK", c, f"claims[{i}].forbidden_here[{j}]", message))
                    break
        for j, name in enumerate(c.private_names):
            if name.strip() and _rx(name).search(u.text):
                message = f'"{name}" is {c.id}.private_names[{j}], a name that must never appear'
                out.append(("VISIBILITY", "BLOCK", c, f"claims[{i}].private_names[{j}]", message))
        if c.visibility == "never" and _support(_weights(u, ctx), frozenset(_tokens(c.summary)))[0] >= ctx.ledger.rules.thresholds.match:
            out.append(("VISIBILITY", "BLOCK", c, f"claims[{i}].visibility", f"describes {c.id}, which is marked visibility: never"))
    return out, negated_ceiling


def _quantity(u: _Unit, k: Claim, i: int, ctx: _Ctx, exact: str | None) -> list[_Hit]:
    """Guard 6c. exact is the allowed sentence on the exact path: its numbers pass the value conditions."""
    values = [q.value for q in k.quantities] + [float(v) for v in k.facts.values() if isinstance(v, int) and not isinstance(v, bool)]
    if not ctx.lint:
        values += [n.value for s in k.allowed for n in numbers(s) if not n.is_vague]
    same = {(n.value, n.has_plus) for n in numbers(exact)} if exact else set()
    out: list[_Hit] = []
    for n in u.nums:
        c = u.clause(n.start)
        clause_states = [s for s, p, t in u.states(False) if u.clause(p) == c]
        top = max((_RANK_6C[s] for s in clause_states), default=None)
        venue_only = top is None and any(u.clause(s) == c for s, e, t in u.found("venue"))
        bad = None
        if n.is_vague:
            bad = f'"{n.raw}" is a vague quantity; it matches no value in {k.id}'
        elif (n.value, n.has_plus) not in same:
            if n.has_plus and not any(v > n.value for v in values):
                bad = f'"{n.raw}" claims more than {n.value:g}; no value in {k.id} is larger'
            elif not n.has_plus and not any(abs(v - n.value) < 1e-9 for v in values):
                bad = f'"{n.raw}" is in none of {k.id}\'s quantities, allowed sentences or facts'
        if bad:
            out.append(("QUANTITY_MISMATCH", "BLOCK", k, f"claims[{i}].quantities", bad))
            continue
        match = [(j, q) for j, q in enumerate(k.quantities) if abs(q.value - n.value) < 1e-9]
        if venue_only and not any(q.venue for j, q in match):
            out.append(("QUANTITY_MISMATCH", "BLOCK", k, f"claims[{i}].quantities", f'"{n.raw}" stands with a venue verb; no quantity of {k.id} names a venue'))
        elif top is not None and match and all(q.state and _RANK_6C[q.state] < top for j, q in match):
            j, q = match[0]
            state = max(clause_states, key=lambda s: _RANK_6C[s])
            out.append(("QUANTITY_MISMATCH", "BLOCK", k, f"claims[{i}].quantities[{j}]", f'"{n.raw}" is true for {q.state}; its clause implies {state}'))
    return out


def _duration(u: _Unit, k: Claim, i: int, ctx: _Ctx) -> list[_Hit]:
    """Guard 6e: durations and calendar years against facts and evidence dates."""
    fs, fe = k.facts.get("start_year"), k.facts.get("end_year")
    fs, fe = (fs, fe) if isinstance(fs, int) and isinstance(fe, int) else (None, None)
    ev = [e.date.year for evs in k.confirmed.values() for e in evs if e.date]
    out: list[_Hit] = []

    def bad(raw: str, why: str, where: str = "facts") -> None:
        out.append(("DURATION_MISMATCH", "BLOCK", k, f"claims[{i}].{where}", f'"{raw}" {why}'))

    for d in u.durs:
        years = d.years if d.years is not None else (ctx.today.year - d.start_year if d.start_year else None)
        lo, hi = d.start_year, d.end_year or ctx.today.year
        if fs is not None and years is not None and years > fe - fs + 1e-9:
            bad(d.raw, f"is {years:g} years; {k.id}.facts give {fe - fs}")
        elif fs is not None and years is not None and d.has_over and not years < fe - fs:
            bad(d.raw, f"says more than {years:g} years; {k.id}.facts give {fe - fs}")
        elif fs is not None and lo and (lo < fs or hi > fe):
            bad(d.raw, f"falls outside {fs}-{fe} in {k.id}.facts")
        elif ev and lo and (lo < min(ev) or (d.end_year or lo) > max(ev)):
            bad(d.raw, f"falls outside the evidence dates of {k.id} ({min(ev)}-{max(ev)})", "confirmed")
    for n in u.years:
        if fs is not None and not fs <= n.value <= fe:
            bad(n.raw, f"falls outside {fs}-{fe} in {k.id}.facts")
        elif ev and not min(ev) <= n.value <= max(ev):
            bad(n.raw, f"falls outside the evidence dates of {k.id} ({min(ev)}-{max(ev)})", "confirmed")
    return out


def _authorship(u: _Unit, k: Claim, i: int) -> list[_Hit]:
    """Guard 6g: the verb or noun class against authorship.role (matrix in docs/ledger.md)."""
    a, out = k.authorship, []
    for cls in ("authored", "led", "contributed", "directed"):
        found = u.found(f"class:{cls}")
        unprovable = cls == "authored" and a.agent_assisted and not a.can_defend_end_to_end
        sev = "BLOCK" if unprovable else _MATRIX[a.role].get(cls)
        if found and sev:
            why = "; agent output you cannot defend end to end is not your proof" if unprovable else ""
            out.append(("AUTHORSHIP", sev, k, f"claims[{i}].authorship", f'"{found[0][2]}" is the {cls} class; {k.id}.authorship.role is {a.role}{why}'))
    return out


def _guards(u: _Unit, k: Claim, ctx: _Ctx, *, exact: bool) -> list[_Hit]:
    """Step 6 against claim k. The exact path runs only 6c, 6h and 6i; lint mode skips 6k."""
    i, out = ctx.pos[k.id], []
    if not exact:
        states = u.states()
        top = max(states, key=lambda x: LADDER.get(x[0], 0), default=None)
        have = max((s for s in k.confirmed if s in LADDER), key=lambda s: LADDER[s], default=None)
        if top and LADDER.get(top[0], 0) > LADDER.get(have or "", 0):
            message = f'"{top[2]}" implies {top[0]}; highest confirmed state of {k.id} is {have or "none"}'
            out.append(("STATE_OVERSTATED", "BLOCK", k, f"claims[{i}].confirmed", message))
        free, implied = u.full(negation_scope=True), {s for s, p, t in states}
        for j, item in enumerate(k.not_confirmed):
            if _overlap(item, free) >= 0.6 or item.strip().lower() in implied:
                out.append(("NOT_CONFIRMED", "BLOCK", k, f"claims[{i}].not_confirmed[{j}]", f'{k.id}.not_confirmed[{j}] "{item}"'))
    out += _quantity(u, k, i, ctx, next((s for s in k.allowed if exact_form(s) == u.norm), None) if exact else None)
    if not exact:
        count = k.beneficiaries.get("count")
        count = int(count) if str(count).isdigit() else None
        plural = [t for s, e, t in u.found("benef") if not u.neg_window(s)]
        if k.kind != "ceiling" and (count is None or count < 2) and plural:
            message = f'"{plural[0]}" is plural; {k.id}.beneficiaries.count is {count if count is not None else "missing"}'
            out.append(("SCOPE_INFLATION", "BLOCK", k, f"claims[{i}].beneficiaries", message))
        allowed = [_rx(a) for a in k.scope_words_allowed if a.strip()]
        for s, e, t in u.found("scope"):
            if not u.neg_clause(s) and not any(rx.search(t) for rx in allowed):
                out.append(("SCOPE_INFLATION", "BLOCK", k, f"claims[{i}].scope_words_allowed", f'"{t}" is not in {k.id}.scope_words_allowed'))
        out += _duration(u, k, i, ctx)
        for j, pattern in enumerate(k.forbidden_here):
            m = (rx := _compile(pattern)) and rx.search(u.text)
            if m:
                out.append(("FORBIDDEN_PHRASE", "BLOCK", k, f"claims[{i}].forbidden_here[{j}]", f'"{m.group(0)}" matches {k.id}.forbidden_here[{j}]'))
        out += _authorship(u, k, i)
    unit_stems = u.full()
    for j, q in enumerate(k.required_qualifiers):
        if q.strip() and not _rx(q).search(u.text) and not (set(_tokens(q)) and set(_tokens(q)) <= unit_stems):
            out.append(("MISSING_QUALIFIER", "REVIEW", k, f"claims[{i}].required_qualifiers[{j}]", f'"{q}" is missing'))
    if k.draft and exact:
        out.append(_draft_flag(k, ctx))
    elif k.draft:
        out.append(("DRAFT_REWORDED", "BLOCK", k, f"claims[{i}].draft", f"{k.id} is a draft; it is accepted only word for word"))
    if exact:
        return out
    pay = u.found("pay")
    if k.visibility == "never":
        out.append(("VISIBILITY", "BLOCK", k, f"claims[{i}].visibility", f"describes {k.id}, which is marked visibility: never"))
    elif "non_cash_value" in k.confirmed and "paid" not in k.confirmed and pay:
        out.append(("VISIBILITY", "BLOCK", k, f"claims[{i}].visibility", f'"{pay[0][2]}" describes non-cash value as pay'))
    if not ctx.lint:
        out += _stale(k, i, ctx)
    out += _unknown_entities(u, k, i, ctx)
    outcomes = u.found("outcome")
    if outcomes and not any(_overlap(o, unit_stems) >= 0.6 for o in k.outcomes_allowed):
        c = u.clause(outcomes[0][0])  # the outcome's clause without state verbs (6b judges those)
        verbs = [s for key, v in u.hits.items() if key.startswith(("state:", "class:")) for s in v]
        said = {stem(w) for s, e, w in u.words if u.clause(s) == c and not any(a < e and s < b for a, b in verbs)}
        nc = next((j for j, item in enumerate(k.not_confirmed) if _overlap(item, said) >= 0.6), None)
        why = f"{k.id}.outcomes_allowed is empty" if not k.outcomes_allowed else f"no entry of {k.id}.outcomes_allowed matches"
        named = f'; {k.id}.not_confirmed[{nc}] "{k.not_confirmed[nc]}" is not confirmed' if nc is not None else ""
        out.append(("UNBACKED_OUTCOME", "BLOCK", k, f"claims[{i}].outcomes_allowed", f'"{outcomes[0][2]}" claims an outcome; {why}{named}'))
    return out


def _draft_flag(k: Claim, ctx: _Ctx) -> _Hit:
    message = f"{k.id} is a draft: your own CV line, not yet backed by evidence; confirm it in ledger.yml"
    return ("DRAFT_CLAIM", "REVIEW", k, f"claims[{ctx.pos[k.id]}].draft", message)


def _stale(k: Claim, i: int, ctx: _Ctx) -> list[_Hit]:
    """Guard 6k: the claim's review_by has passed, or person.as_of is older than stale_after_days."""
    person = ctx.ledger.person
    as_of, days = person.get("as_of"), person.get("stale_after_days", 365)
    as_of = dt.date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    if k.review_by and k.review_by < ctx.today:
        return [("STALE", "REVIEW", k, f"claims[{i}].review_by", f"{k.id}.review_by {k.review_by} has passed")]
    if isinstance(as_of, dt.date) and isinstance(days, int) and as_of + dt.timedelta(days=days) < ctx.today:
        return [("STALE", "REVIEW", k, "person.as_of", f"person.as_of {as_of} is older than {days} days")]
    return []


def _unknown_entities(u: _Unit, k: Claim, i: int, ctx: _Ctx) -> list[_Hit]:
    """Guard 6l: capitalized words after the sentence start that the claim's own text does not name."""
    voc, stop = vocab(), stopwords()
    known = {_word(w) for t in [k.summary, k.statement, *k.allowed, *k.private_names] for w in _WORD.findall(normalize(t))}
    known |= {stem(w) for w in known}
    skill_spans = [(s, e) for name, s, e in skill_terms(u.text, skills_line=True)]
    unknown = []
    for idx, (s, e, w) in enumerate(u.words):
        before = u.text[:s].rstrip(" \"'([")
        if not u.text[s].isupper() or idx == 0 or not before or before[-1] in ".!?":
            continue
        if w in stop or w in voc.not_entities or w in known or stem(w) in known or w in ctx.company:
            continue
        if any(a <= s and e <= b for a, b in skill_spans) or any(p.fullmatch(w) for p in voc.groups["language"]):
            continue
        unknown.append(u.text[s:e])
    if not unknown:
        return []
    return [("UNKNOWN_ENTITY", "REVIEW", k, f"claims[{i}]", f"{', '.join(unknown)}: not in {k.id}'s summary, statement, allowed sentences or private_names")]


def guard_allowed(ledger: Ledger, today: dt.date) -> list[tuple[int, int, str, str, str]]:
    """The step-6 guards on every allowed sentence of the non-draft, non-ceiling claims, against their own
    claim, as lint runs them: 6c compares with quantities and facts only, 6k does not run.
    Returns (claim index, sentence index, rule, severity, message)."""
    ctx = _Ctx.build(ledger, today, lint=True)
    return [(i, j, rule, sev, msg) for i, c in enumerate(ledger.claims) if not c.draft and c.kind != "ceiling"
            for j, s in enumerate(c.allowed) for rule, sev, _, _, msg in _guards(_unit(0, s, ctx), c, ctx, exact=False)]  # fmt: skip


# ---------------------------------------------------------------- attribution and the document check


def _attribute(u: _Unit, ctx: _Ctx, negated_ceiling: Claim | None) -> tuple[str, Claim | None, list[_Hit]]:
    """Step 5. Returns (path, claim, flags); path is exact, near, support, ambiguous, cited, ceiling or none."""
    claims, t = ctx.ledger.claims, ctx.ledger.rules.thresholds
    for c in claims:
        if c.kind == "ceiling" and u.norm in {exact_form(s) for s in c.allowed}:
            return "ceiling", c, []
    flags: list[_Hit] = []
    candidates = [c for c in claims if c.kind != "ceiling"]
    if u.cites is not None:
        flags += [("CITATION_MISMATCH", "BLOCK", None, cid, f'the marker cites "{cid}", which is not a claim id in the ledger')
                  for cid in u.cites if cid not in ctx.by_id]  # fmt: skip
        candidates = [c for c in candidates if c.id in u.cites]
        if not candidates:
            return "none", None, flags
    for c in candidates:
        if u.norm in {exact_form(s) for s in c.allowed}:
            return "exact", c, flags
    ratios = [(difflib.SequenceMatcher(None, u.norm, exact_form(s)).ratio(), c.id, c) for c in candidates for s in c.allowed]
    best = min(ratios, key=lambda r: (-r[0], r[1]), default=None)
    if best and best[0] >= t.exact:
        return "near", best[2], flags
    weights = _weights(u, ctx)
    scored = sorted(((*_support(weights, ctx.stems[c.id]), c) for c in candidates), key=lambda x: (-x[0], -x[1], x[2].id))
    support, shared, k = scored[0] if scored else (0.0, 0, None)
    if u.cites is not None:
        if k is None or support < t.ambiguous:
            fld = f"claims[{ctx.pos[k.id]}]" if k else None
            flags.append(("CITATION_MISMATCH", "BLOCK", k, fld, f"support {support:.2f} for the cited claim is below {t.ambiguous}"))
            return "none", None, flags
        return "cited", k, flags
    if k is not None and support >= t.match:
        return "support", k, flags
    if k is not None and support >= t.ambiguous:
        flags.append(("AMBIGUOUS", "REVIEW", k, f"claims[{ctx.pos[k.id]}]", f"support {support:.2f} for {k.id} is between {t.ambiguous} and {t.match}"))
        return "ambiguous", k, flags
    if negated_ceiling is not None:
        return "ceiling", negated_ceiling, flags
    flags.append(("UNBACKED", "BLOCK", None, None, "no ledger claim supports this line"))
    return "none", None, flags


def _skills(u: _Unit, k: Claim | None, ctx: _Ctx, draft: Claim | None = None) -> list[_Hit]:
    """Step 4. A skills line (k None): every term needs a non-draft claim's skills, or those of draft, the draft
    claim the line repeats word for word. Elsewhere: the matched claim's."""
    if k is None:
        known = {canonical_skill(s) for c in ctx.ledger.claims if not c.draft or c is draft for s in c.skills}
        drafted = {canonical_skill(s) for c in ctx.ledger.claims if c.draft for s in c.skills}
        names = dict.fromkeys(n for n, s, e in skill_terms(u.text, skills_line=True))
        return [("UNBACKED_SKILL", "BLOCK", None, None, f'"{n}" is only in draft claims\' skills; a draft backs only its own line, word for word'
                 if n in drafted else f'"{n}" is in no claim\'s skills') for n in names if n not in known]  # fmt: skip
    own = {canonical_skill(s) for s in k.skills}
    names = dict.fromkeys(n for n, s, e in skill_terms(u.text, skills_line=bool(_labelled(u))))
    return [("UNBACKED_SKILL", "BLOCK", k, None, f'"{n}" is not in {k.id}.skills') for n in names if n not in own]


def _check_unit(line: int, raw: str, ctx: _Ctx, require_markers: bool) -> tuple[list[_Hit], bool, bool]:
    """(flags, claim-bearing, backed) for one unit."""
    u = _unit(line, raw, ctx)
    flags, negated_ceiling = _doc_guards(u, ctx)
    if require_markers and u.cites is None:
        return flags + [("UNBACKED", "BLOCK", None, None, "a tailored line must carry a citation marker <!-- c:ID -->")], True, False
    if _is_skills_line(u):
        draft = next((c for c in ctx.ledger.claims if c.draft and u.norm in {exact_form(s) for s in c.allowed}), None)
        hits = _skills(u, None, ctx, draft)
        return flags + hits + ([_draft_flag(draft, ctx)] if draft else []), True, not hits
    if _nonclaim(u, ctx):
        return flags, False, False
    path, k, attribution = _attribute(u, ctx, negated_ceiling)
    if not _signal(u) and not _matched(path, k):
        return flags, False, False
    flags += attribution
    if k is None or path == "ceiling":
        return flags, True, k is not None
    if path != "exact":
        flags += _skills(u, k, ctx)
    return flags + _guards(u, k, ctx, exact=path == "exact"), True, path != "ambiguous"


def _safe_rewrite(claim: Claim | None, unit_text: str) -> str | None:
    if claim is None or claim.visibility == "never" or not claim.allowed:
        return None
    full = set(_tokens(unit_text))
    return max(claim.allowed, key=lambda s: (_overlap(s, full), -claim.allowed.index(s)))


def _gaps(ctx: _Ctx, offer: Offer | None, requirements: list[Requirement] | None) -> list[str]:
    """Step 7: offer sentences a ceiling blocks come first, then must-haves no non-draft claim lists in skills."""
    out: list[str] = []
    if offer is not None:
        for c in (c for c in ctx.ledger.claims if c.kind == "ceiling"):
            for line, sentence in sentences(offer.description):
                if any((rx := _compile(p)) and rx.search(sentence) for p in c.blocks_requirements):
                    out.append(f'{c.id}: "{sentence}"')
    musts = [r.term for r in requirements if r.must] if requirements is not None else list(offer.skills if offer else [])
    backed = {canonical_skill(s) for c in ctx.ledger.claims if not c.draft for s in c.skills}
    out += [t for t in musts if canonical_skill(t) not in backed]
    return list(dict.fromkeys(out))


def _document_text(path: Path, raw: bytes) -> str:
    if path.suffix.lower() in (".pdf", ".docx"):
        from honestcv.profile import load_cv_text  # reads PDF and DOCX

        return load_cv_text(path).text
    return normalize(raw.decode("utf-8-sig", errors="replace"))


def check_document(
    path: Path,
    ledger: Ledger,
    *,
    offer: Offer | None = None,
    requirements: list[Requirement] | None = None,
    require_markers: bool = False,
    ws_root: Path,
    today: dt.date,
) -> CheckResult:
    """Check the file as it is on disk. require_markers is tailor mode: every unit needs a <!-- c:ID --> marker,
    and headings (honestcv writes them) are not units."""
    path = Path(path)
    raw = path.read_bytes()
    units = document_units(_document_text(path, raw), headings=not require_markers)
    lint = lint_ledger(ledger, Path(ws_root), today)
    base = {"units_total": len(units), "lint": lint, "document_path": home_relative(path), "document_sha256": hashlib.sha256(raw).hexdigest()}
    errors = [x for x in lint if x.severity == "ERROR"]
    if errors:
        flags = [CheckFlag(rule="LEDGER_LINT", severity="BLOCK", line=x.line or 0, quote=x.path, claim_id=None, field=x.path,
                           message=x.message, safe_rewrite=None) for x in errors]  # fmt: skip
        return CheckResult(verdict="BLOCK", flags=flags, units_claim_bearing=0, units_backed=0, gaps=[], **base)
    ctx = _Ctx.build(ledger, today, offer)
    flags, bearing, backed = [], 0, 0
    for line, raw_unit in units:
        hits, is_bearing, is_backed = _check_unit(line, raw_unit, ctx, require_markers)
        bearing, backed = bearing + is_bearing, backed + (is_bearing and is_backed)
        quote, seen = " ".join(strip_markers(raw_unit).split()), set()
        for rule, severity, claim, fld, message in hits:
            if (rule, severity, claim and claim.id, fld, message) not in seen:
                seen.add((rule, severity, claim and claim.id, fld, message))
                flags.append(CheckFlag(rule=rule, severity=severity, line=line, quote=quote, claim_id=claim.id if claim else None,
                                       field=fld, message=message, safe_rewrite=_safe_rewrite(claim, quote)))  # fmt: skip
    severities = {f.severity for f in flags}
    verdict = "BLOCK" if "BLOCK" in severities else "REVIEW" if severities else "PASS"
    return CheckResult(verdict=verdict, flags=flags, units_claim_bearing=bearing, units_backed=backed,
                       gaps=_gaps(ctx, offer, requirements), **base)  # fmt: skip


# ---------------------------------------------------------------- rendering


def _count(n: int, what: str) -> str:
    return f"{n} line{'' if n == 1 else 's'} {what}"


def summary_line(result: CheckResult) -> str:
    """'BLOCK · 4 lines blocked · 0 lines to review · 1 line passed'. Passed lines are claim-bearing lines
    without a flag."""
    if any(f.rule == "LEDGER_LINT" for f in result.flags):
        n = len(result.flags)
        return f"BLOCK · the ledger has {n} error{'' if n == 1 else 's'}, so no line was checked"
    units: dict[tuple[int, str], set[str]] = {}
    for f in result.flags:
        units.setdefault((f.line, f.quote), set()).add(f.severity)
    blocked = sum(1 for s in units.values() if "BLOCK" in s)
    passed = max(0, result.units_claim_bearing - len(units))
    return f"{result.verdict} · {_count(blocked, 'blocked')} · {_count(len(units) - blocked, 'to review')} · {_count(passed, 'passed')}"


def render_check(result: CheckResult, *, markdown: bool = False) -> str:
    """Text for check.md and the CLI: the verdict, then each flagged line with its rules, ledger field paths and
    a safe rewrite from the ledger, then the gaps. markdown escapes the quoted text and the offer's gaps."""
    text = str
    if markdown:
        from honestcv.report.markdown import esc as text
    out = [summary_line(result), ""]
    if any(f.rule == "LEDGER_LINT" for f in result.flags):
        out.append("Fix these ledger errors first (honestcv ledger lint shows them all):")
        out += [f"  ERROR  line {f.line}  {f.field}: {text(f.message)}" for f in result.flags]
    groups: dict[tuple[int, str], list[CheckFlag]] = {}
    for f in (f for f in result.flags if f.rule != "LEDGER_LINT"):
        groups.setdefault((f.line, f.quote), []).append(f)
    for (line, quote), flags in groups.items():
        out.append(f'line {line}  "{text(quote)}"')
        for f in flags:
            review = " (REVIEW)" if f.severity == "REVIEW" else ""
            out.append(f"  {f.rule:<18} {text(f.message)}{review}" + (f"  [{f.field}]" if f.field else ""))
        safe = Counter(f.safe_rewrite for f in flags if f.safe_rewrite).most_common(1)
        if safe:
            out.append(f'  safe: "{text(safe[0][0])}"')
    if result.gaps:
        out += ["", "Offer requirements your ledger does not back (not added anywhere):"] + [f"  - {text(g)}" for g in result.gaps]
    warns = sum(1 for x in result.lint if x.severity == "WARN")
    if warns:
        out += ["", f"Ledger: {warns} warning{'' if warns == 1 else 's'}. Run: honestcv ledger lint"]
    return "\n".join(out).rstrip() + "\n"
