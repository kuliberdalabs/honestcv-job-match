"""Pydantic v2 data models shared by every module. Changing a model changes the contract between modules.

Models for files a person writes (config preferences, ledger) reject unknown keys, so a typo is reported
instead of silently ignored. Models for data honestcv produces (offers, results, runs) ignore unknown keys.
"""

import datetime as dt
import math
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

Level = Literal["intern", "junior", "mid", "senior", "lead"]
Layer = Literal["CORE", "STRETCH", "DOWNSHIFT"]
Remote = Literal["remote", "hybrid", "onsite"]
Contract = Literal["employment", "b2b", "contract", "freelance", "internship"]
State = Literal["generated", "built", "tested", "shipped", "used", "acknowledged", "paid", "non_cash_value"]
Severity = Literal["BLOCK", "REVIEW"]

_STRICT_KEYS = ConfigDict(extra="forbid")

# C0 controls except tab and newline, DEL and the C1 controls. ESC starts the sequences that move the cursor, clear the
# screen, set a window title or write to the clipboard when a terminal prints them.
# Lone surrogates (from a JSON "\ud800" escape) and the non-characters U+FFFE/U+FFFF go too: they cannot be written as UTF-8.
_CONTROLS = dict.fromkeys([*range(0x09), *range(0x0B, 0x20), 0x7F, *range(0x80, 0xA0), *range(0xD800, 0xE000), 0xFFFE, 0xFFFF], None)

def strip_controls(text: str) -> str:
    """Text without control characters, lone surrogates and non-characters. A carriage return counts as a line end;
    tab and newline stay."""
    return text.replace("\r\n", "\n").replace("\r", "\n").translate(_CONTROLS)

def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return strip_controls(value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):  # an item that was nothing but control characters is dropped
        return [c for c in map(_clean, value) if not (isinstance(c, str) and not c.strip())]
    return value

class _Untrusted(BaseModel):
    """Base of the models that hold text from outside (job boards, files, e-mails). Whatever builds one, from an
    adapter, an importer or a JSON file, every string in it comes out without control characters."""

    @model_validator(mode="before")
    @classmethod
    def _strip_control_characters(cls, data: Any) -> Any:
        return _clean(data) if isinstance(data, dict) else data

class Salary(_Untrusted):
    min: float | None = None
    max: float | None = None
    currency: str | None = None  # ISO 4217, upper case
    unit: Literal["hour", "day", "month", "year"] | None = None
    unit_assumed: bool = False  # true when the unit was inferred from the amount

    @field_validator("min", "max")
    @classmethod
    def _finite(cls, value: float | None) -> float | None:
        return value if value is None or math.isfinite(value) else None  # "Infinity" and "NaN" mean "not stated"

class Offer(_Untrusted):
    id: str  # first 10 hex of sha1(dedupe_key)
    source: str  # registry id, or "import:csv|json|eml|html|text"
    title: str
    company: str | None = None
    url: str | None = None  # listing URL after clean_url(): tracking parameters and fragment removed
    apply_url: str | None = None
    other_urls: list[str] = []  # same requisition seen elsewhere
    location: str | None = None
    countries: list[str] = []  # ISO 3166 alpha-2
    remote: Remote | None = None
    contract: list[Contract] = []
    level: Level | None = None  # only if the source states it
    skills: list[str] = []  # structured must-haves, canonical names
    skills_nice: list[str] = []  # structured nice-to-haves
    languages: list[str] = []  # required languages, ISO 639-1
    salary: Salary | None = None
    description: str = ""  # plain text
    posted_at: dt.date | None = None
    valid_through: dt.date | None = None
    requisition_id: str | None = None
    attribution: str  # "Himalayas", "RemoteOK", "your file alerts.eml"
    thin: bool = False  # title + link only (typical for alert e-mails)
    possible_duplicate_of: str | None = None

class Profile(BaseModel):
    cv_id: str  # file name, e.g. "maya-analytics.md"
    cv_sha256: str
    skills: list[str]  # canonical; after skills_add / skills_remove
    titles: list[str]  # job titles found in the CV
    target_titles: list[str]  # from config; falls back to titles
    years: float | None  # profile.years, else counted from experience date ranges
    level: Level | None  # profile.level only; never guessed
    languages: dict[str, str]  # {"en": "C1", "sv": "native"}
    experience: list[tuple[str, str]] = []  # (title, "2017-2020") found under experience headings
    pages: int | None = None  # PDF page count; None for other formats
    warnings: list[str] = []  # reading problems shown by init and add-cv (bad Polish letters, no text layer)

class CvText(BaseModel):
    """Result of profile.load_cv_text."""

    text: str
    pages: int | None = None
    warnings: list[str] = []

class Money(BaseModel):
    model_config = _STRICT_KEYS
    amount: float = Field(allow_inf_nan=False)
    currency: str
    unit: Literal["hour", "day", "month", "year"] = "month"

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.strip().upper()  # "sek" and "SEK" mean the same

def _fold(name: str) -> str:
    """Lower case without accents, so "España", "espana" and "ESPAÑA" are one key."""
    decomposed = unicodedata.normalize("NFKD", name.casefold().replace("\u0142", "l"))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())

_COUNTRY_NAMES = {
    _fold(name): code
    for code, names in (
        ("PL", ("Polska", "Poland")), ("SE", ("Sverige", "Sweden", "Szwecja")), ("DE", ("Deutschland", "Germany", "Niemcy")),
        ("GB", ("United Kingdom", "Great Britain", "Wielka Brytania")), ("US", ("United States", "USA", "Stany Zjednoczone")),
        ("NL", ("Netherlands", "Nederland", "Holandia")), ("ES", ("Spain", "España", "Hiszpania")), ("FR", ("France", "Francja")),
        ("CZ", ("Czechia", "Czech Republic", "Czechy")), ("IE", ("Ireland", "Irlandia")), ("NO", ("Norway", "Norge", "Norwegia")),
        ("DK", ("Denmark", "Danmark", "Dania")), ("FI", ("Finland", "Suomi", "Finlandia")), ("AT", ("Austria", "Österreich")),
        ("CH", ("Switzerland", "Schweiz", "Szwajcaria")), ("PT", ("Portugal", "Portugalia")), ("IT", ("Italy", "Italia", "Włochy")),
    )
    for name in names
}  # fmt: skip

class Preferences(BaseModel):
    model_config = _STRICT_KEYS
    remote: list[Remote] = []
    countries: list[str] = []
    cities: list[str] = []
    contract: list[Contract] = []
    min_salary: list[Money] = []  # one entry per currency; compared in the offer's own currency
    deny_titles: list[str] = []  # hit() token syntax
    deny_terms: list[str] = []
    freshness_days: int = 30

    @field_validator("countries")
    @classmethod
    def _country_codes(cls, value: list[str]) -> list[str]:
        out = []
        for name in value:
            text = name.strip()
            two_letters = len(text) == 2 and text.isascii() and text.isalpha()
            code = ("GB" if text.upper() == "UK" else text.upper()) if two_letters else _COUNTRY_NAMES.get(_fold(text))
            if code is None:
                raise PydanticCustomError("country_code", "Use two-letter country codes, for example PL or SE")
            out.append(code)  # "se" and "SE" mean the same
        return out

class OutputConfig(BaseModel):
    """The output: block of honestcv.yml."""

    model_config = _STRICT_KEYS
    csv_delimiter: Literal[",", ";", "\t"] = ","

# ---- textutil results ----
class NumberMention(BaseModel):
    value: float  # -1.0 for a vague quantity ("dozens"), which matches no ledger value
    raw: str
    start: int
    has_plus: bool
    is_year: bool
    is_percent: bool
    is_vague: bool

class DurationMention(BaseModel):
    raw: str
    start: int
    years: float | None
    start_year: int | None
    end_year: int | None
    has_over: bool

# ---- ledger ----
class Evidence(BaseModel):
    model_config = _STRICT_KEYS
    type: Literal[
        "repo", "file", "url", "note", "test_record", "handover_record", "usage_evidence", "recipient_statement",
        "invoice_or_transfer", "file_list"
    ]
    ref: str  # workspace-relative path, URL, or note text (type note only)
    date: dt.date | None = None

class Quantity(BaseModel):
    model_config = _STRICT_KEYS
    name: str
    value: float
    unit: str
    state: State | None = None  # the state this number is true for
    venue: str | None = None

class Authorship(BaseModel):
    model_config = _STRICT_KEYS
    role: Literal["sole", "led", "contributed", "directed"] = "sole"
    agent_assisted: bool = False
    can_defend_end_to_end: bool = True

class Claim(BaseModel):
    model_config = _STRICT_KEYS
    id: str  # slug, unique
    kind: Literal["delivery", "artifact", "output", "ceiling", "biography", "skill"]
    summary: str = ""
    statement: str = ""  # ceiling and biography kinds
    skills: list[str] = []  # canonical skills this claim evidences
    confirmed: dict[State, list[Evidence]] = {}
    not_confirmed: list[str] = []
    quantities: list[Quantity] = []
    facts: dict[str, int | str] = {}  # biography: start_year, end_year, precision
    beneficiaries: dict[str, int | str] = {}  # {"count": 3, "kind": "support teams"}
    scope_words_allowed: list[str] = []  # e.g. ["production"] if evidenced
    outcomes_allowed: list[str] = []  # outcome phrases the evidence supports (UNBACKED_OUTCOME)
    authorship: Authorship = Authorship()
    allowed: list[str] = []  # pre-approved sentences
    required_qualifiers: list[str] = []
    forbidden_here: list[str] = []  # regexes
    blocks_requirements: list[str] = []  # ceiling kind: regexes that gate offers
    private_names: list[str] = []  # names that must never appear in output
    visibility: Literal["cv", "never"] = "cv"
    draft: bool = False
    draft_source: str | None = None  # "cvs/cv-2026.pdf#line 12"
    review_by: dt.date | None = None
    # field path in flags and lint: claims[i].<field>, e.g. claims[1].outcomes_allowed

class ForbiddenPhrase(BaseModel):
    model_config = _STRICT_KEYS
    pattern: str
    reason: str = ""

class Thresholds(BaseModel):
    model_config = _STRICT_KEYS
    exact: float = 0.90
    match: float = 0.60
    ambiguous: float = 0.35

class LedgerRules(BaseModel):
    model_config = _STRICT_KEYS
    forbidden_phrases: list[ForbiddenPhrase] = []
    thresholds: Thresholds = Thresholds()
    verbs_extra: dict[State, list[str]] = {}

class Ledger(BaseModel):
    model_config = _STRICT_KEYS
    schema_version: Literal[1]
    person: dict  # name (optional), as_of, stale_after_days
    rules: LedgerRules = LedgerRules()  # forbidden_phrases, thresholds, verbs_extra
    claims: list[Claim] = []

class LedgerIndex(BaseModel):  # built by ledger.py, consumed by scoring.py
    skill_backing: dict[str, list[str]]  # skill -> claim ids (non-draft, any confirmed state)
    draft_skills: dict[str, list[str]]
    ceiling_patterns: list[tuple[str, str]]  # (claim_id, regex) from blocks_requirements

class LintIssue(BaseModel):
    severity: Literal["ERROR", "WARN"]
    path: str  # "claims[0].confirmed.shipped"
    line: int | None
    message: str

# ---- scoring ----
class Requirement(BaseModel):
    term: str
    must: bool
    in_profile: bool
    backed_by: list[str] = []  # claim ids
    quote: str | None = None

class CriterionScore(BaseModel):
    id: str
    weight: int
    applicable: bool
    fraction: float  # 0..1
    points: float  # contribution to base, after redistribution
    note: str  # "4 of 6 required skills, 1 of 2 nice-to-have"
    quotes: list[str] = []  # offer snippets, each <= 120 chars

class Penalty(BaseModel):
    code: str
    points: int  # negative
    quote: str | None = None

class Gate(BaseModel):
    code: Literal[
        "deny_title", "deny_term", "commission_only", "unpaid_task", "equity_only", "expired", "stale",
        "no_content", "ledger_ceiling"
    ]
    detail: str
    quote: str | None = None

class MatchResult(BaseModel):
    offer_id: str
    cv_id: str
    gate: Gate | None
    layer: Layer
    layer_assumed: bool  # offer level unknown
    criteria: list[CriterionScore]
    penalties: list[Penalty]
    base: float
    match_pct: int
    unknowns: list[str]
    requirements: list[Requirement]
    requirements_recognized: int  # requirement terms found; < 3 makes the result low confidence
    backed: tuple[int, int]  # (matched requirements with ledger backing, matched requirements)
    surfaced: bool
    low_confidence: bool
    band: Literal["strong", "good", "fair"] | None

# ---- checker ----
RuleCode = Literal[
    "LEDGER_LINT", "UNBACKED", "AMBIGUOUS", "CITATION_MISMATCH", "UNBACKED_SKILL", "STATE_OVERSTATED",
    "NOT_CONFIRMED", "QUANTITY_MISMATCH", "SCOPE_INFLATION", "DURATION_MISMATCH", "FORBIDDEN_PHRASE",
    "CEILING_BREACH", "VISIBILITY", "AUTHORSHIP", "DRAFT_REWORDED", "DRAFT_CLAIM", "MISSING_QUALIFIER", "STALE",
    "UNKNOWN_ENTITY", "UNBACKED_OUTCOME"
]

class CheckFlag(BaseModel):
    rule: RuleCode
    severity: Severity
    line: int
    quote: str
    claim_id: str | None
    field: str | None  # ledger field path, e.g. "claims[1].quantities[0]"
    message: str
    safe_rewrite: str | None

# The "schema" key is stored in the field schema_. Dumps write "schema" (serialize_by_alias, pydantic >= 2.11);
# pass by_alias=True as well when dumping, so the output is the same on any pydantic 2 release.
_SCHEMA_ALIAS = ConfigDict(populate_by_name=True, serialize_by_alias=True)

class CheckResult(BaseModel):
    model_config = _SCHEMA_ALIAS
    schema_: Literal["honestcv.check.v1"] = Field("honestcv.check.v1", alias="schema")
    verdict: Literal["PASS", "REVIEW", "BLOCK"]
    flags: list[CheckFlag]
    units_total: int
    units_claim_bearing: int
    units_backed: int
    lint: list[LintIssue]
    gaps: list[str]  # offer must-haves without ledger backing
    document_path: str
    document_sha256: str

# ---- run ----
class SourceStatus(BaseModel):
    id: str
    state: Literal["ok", "cached", "empty", "drift", "refused", "error", "skipped"]
    http_status: int | None
    offers: int
    message: str

class ScanRun(BaseModel):
    model_config = _SCHEMA_ALIAS
    schema_: Literal["honestcv.scan.v1"] = Field("honestcv.scan.v1", alias="schema")
    run_id: str
    today: dt.date
    status: Literal["PASS", "PARTIAL", "FAIL"]
    sources: list[SourceStatus]
    cvs: list[str]
    offers: list[Offer]
    results: list[MatchResult]
    gate_counts: dict[str, int]
    new_offer_ids: list[str]
    config_digest: str  # sha256 of the merged config
    ledger_present: bool
