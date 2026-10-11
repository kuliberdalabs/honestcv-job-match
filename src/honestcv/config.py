"""Load and validate honestcv.yml. Every key is optional; a missing key takes the value of the file init writes.

YAML mistakes and invalid values raise ConfigError with the line number and an example of the right shape.
Messages never echo values from the file except criterion and penalty names.
"""

import copy
import datetime as dt
import difflib
import functools
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from honestcv.errors import ConfigError, format_loc, validation_messages
from honestcv.models import Layer, Level, OutputConfig, Preferences

CRITERION_TYPES = ("contract", "language", "level", "location", "salary", "skills_coverage", "title_match", "tokens")
PENALTY_CODES = ("hard_requirement_missing", "years_gap")

_DEFAULT_CONFIG_TEXT = """\
# honestcv.yml: every key is optional. Delete what you do not need.
profile:
  level: null            # intern | junior | mid | senior | lead. null = level is not scored (never guessed)
  years: null            # years of experience. null = counted from date ranges under experience headings
  target_titles: []      # e.g. ["data analyst", "analityk danych"]; list both languages. empty = the score uses titles from your CV; no title is sent as a search term
  skills_add: []         # skills the CV reader missed
  skills_remove: []      # skills it found that you do not want matched

preferences:             # anything left empty is simply not scored
  remote: []             # any of: remote, hybrid, onsite
  countries: []          # two-letter codes, e.g. [PL, SE]; names such as Poland work too
  cities: []
  contract: []           # any of: employment, b2b, contract, freelance, internship
  min_salary: []         # one entry per currency, e.g. [{amount: 12000, currency: PLN, unit: month}, {amount: 3000, currency: EUR, unit: month}]
  deny_titles: []        # offers whose title matches are filtered out, e.g. ["platform engineer", "sales*"]
  deny_terms: []         # same for the offer text
  freshness_days: 30     # older offers are filtered out

search:
  keywords: []           # empty = your target_titles, then your top 5 skills
  sources: [himalayas, remoteok, weworkremotely, arbeitnow]
  companies:             # public job boards of companies you name; nothing is discovered for you
    greenhouse: []       # board tokens, e.g. [examplecorp]
    lever: []            # site names
    lever_eu: []
    ashby: []

output:
  csv_delimiter: ","      # ";" if Excel shows everything in one column (init picks it from your system settings)

scoring:
  criteria:              # weights must sum to 100
    - {id: skills,   type: skills_coverage, weight: 45, nice_to_have_credit: 0.5}
    - {id: title,    type: title_match,     weight: 15, threshold: 2}
    - {id: level,    type: level,           weight: 10, points: {CORE: 1.0, STRETCH: 0.5, DOWNSHIFT: 0.5}}
    - {id: location, type: location,        weight: 10}
    - {id: salary,   type: salary,          weight: 10, near_ratio: 0.8}
    - {id: contract, type: contract,        weight: 5}
    - {id: language, type: language,        weight: 5}
    # Add your own criterion, for example:
    # - {id: domain, type: tokens, weight: 10, tokens: ["logistic*", "e-commerce", "retail"], threshold: 1}
  penalties:
    - {code: years_gap, steps: [{min_gap: 1, points: -5}, {min_gap: 3, points: -10}]}
    - {code: hard_requirement_missing, points: -10}
  surface:               # when an offer is shown, per layer
    CORE:      {min_match: 50}
    STRETCH:   {min_match: 50}
    DOWNSHIFT: {min_match: 45}
  bands: {strong: 75, good: 60}     # colour only; never overrides surface
  min_applicable_weight: 50          # below this the result is low confidence: listed, never ranked
"""


class _Loader(yaml.SafeLoader):
    """SafeLoader where only true/false are booleans, so a country code like NO stays text."""


_Loader.yaml_implicit_resolvers = {
    key: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


@functools.cache
def _template() -> dict:
    return yaml.load(_DEFAULT_CONFIG_TEXT, Loader=_Loader)


_STRICT = ConfigDict(extra="forbid")


class ProfileOverrides(BaseModel):
    model_config = _STRICT
    level: Level | None = None  # never guessed
    years: float | None = None  # None = counted from date ranges under experience headings
    target_titles: list[str] = []
    skills_add: list[str] = []
    skills_remove: list[str] = []


class CompaniesConfig(BaseModel):
    model_config = _STRICT
    greenhouse: list[str] = []  # board tokens
    lever: list[str] = []  # site names
    lever_eu: list[str] = []
    ashby: list[str] = []


class SearchConfig(BaseModel):
    model_config = _STRICT
    keywords: list[str] = []  # empty = target_titles typed in honestcv.yml, then the top 5 skills
    sources: list[str] = ["himalayas", "remoteok", "weworkremotely", "arbeitnow"]
    companies: CompaniesConfig = CompaniesConfig()
    weworkremotely_feeds: list[str] = []  # extra category feeds, e.g. ["remote-data-science-jobs"]; not in the template


class Criterion(BaseModel):
    model_config = _STRICT
    id: str
    type: str  # one of CRITERION_TYPES, checked by validate_scoring with a friendly message
    weight: int
    nice_to_have_credit: float = 0.5  # skills_coverage
    threshold: int | None = None  # title_match (default 2) and tokens (default 1)
    points: dict[Layer, float] = Field(default_factory=lambda: {"CORE": 1.0, "STRETCH": 0.5, "DOWNSHIFT": 0.5})  # level
    near_ratio: float = 0.8  # salary
    tokens: list[str] = []  # tokens, in hit() syntax

    @model_validator(mode="after")
    def _type_defaults(self) -> "Criterion":
        if self.threshold is None:
            self.threshold = {"title_match": 2, "tokens": 1}.get(self.type)
        return self


class PenaltyStep(BaseModel):
    model_config = _STRICT
    min_gap: float
    points: int


class PenaltyRule(BaseModel):
    model_config = _STRICT
    code: str  # one of PENALTY_CODES
    points: int | None = None  # hard_requirement_missing
    steps: list[PenaltyStep] = []  # years_gap


class SurfaceRule(BaseModel):
    model_config = _STRICT
    min_match: int


class SurfaceConfig(BaseModel):
    model_config = _STRICT
    CORE: SurfaceRule = SurfaceRule(min_match=50)
    STRETCH: SurfaceRule = SurfaceRule(min_match=50)
    DOWNSHIFT: SurfaceRule = SurfaceRule(min_match=45)

    def min_match(self, layer: Layer) -> int:
        return getattr(self, layer).min_match


class Bands(BaseModel):
    model_config = _STRICT
    strong: int = 75
    good: int = 60


class ScoringConfig(BaseModel):
    model_config = _STRICT
    criteria: list[Criterion] = Field(default_factory=lambda: [Criterion(**c) for c in copy.deepcopy(_template()["scoring"]["criteria"])])
    penalties: list[PenaltyRule] = Field(default_factory=lambda: [PenaltyRule(**p) for p in copy.deepcopy(_template()["scoring"]["penalties"])])
    surface: SurfaceConfig = SurfaceConfig()
    bands: Bands = Bands()  # colour only; never overrides surface
    min_applicable_weight: int = 50  # below this the result is low confidence


class Config(BaseModel):
    model_config = _STRICT
    profile: ProfileOverrides = ProfileOverrides()
    preferences: Preferences = Preferences()
    search: SearchConfig = SearchConfig()
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    output: OutputConfig = OutputConfig()
    demo_today: dt.date | None = None  # read only by `demo`


def default_config_text() -> str:
    """The commented honestcv.yml that init writes."""
    return _DEFAULT_CONFIG_TEXT


# ---- friendly errors ----

_EXAMPLES = {
    "level": "level: mid", "years": "years: 6", "target_titles": 'target_titles: ["data analyst", "analityk danych"]',
    "skills_add": "skills_add: [sql, excel]", "skills_remove": "skills_remove: [excel]", "remote": "remote: [remote, hybrid]",
    "countries": "countries: [PL, SE]", "cities": "cities: [Gothenburg]", "contract": "contract: [employment, b2b]",
    "min_salary": "min_salary: [{amount: 12000, currency: PLN, unit: month}]", "deny_titles": 'deny_titles: ["platform engineer"]',
    "deny_terms": 'deny_terms: ["commission"]', "freshness_days": "freshness_days: 30", "keywords": 'keywords: ["data analyst", sql]',
    "sources": "sources: [himalayas, remoteok, weworkremotely, arbeitnow]", "companies": "companies: {greenhouse: [examplecorp]}",
    "csv_delimiter": 'csv_delimiter: ";"', "criteria": "- {id: skills, type: skills_coverage, weight: 45}",
    "penalties": "- {code: hard_requirement_missing, points: -10}", "surface": "CORE: {min_match: 50}",
    "bands": "bands: {strong: 75, good: 60}", "demo_today": "demo_today: 2026-10-10",
}  # fmt: skip
_SECTIONS = {(): Config, ("profile",): ProfileOverrides, ("preferences",): Preferences, ("search",): SearchConfig,
             ("search", "companies"): CompaniesConfig, ("output",): OutputConfig, ("scoring",): ScoringConfig}  # fmt: skip


def _friendly(loc: tuple, problem: str) -> str:
    where = format_loc(loc) or "honestcv.yml"
    name = next((p for p in reversed(loc) if isinstance(p, str)), None)
    if problem == "is not a known key" and name is not None:
        section = _SECTIONS.get(tuple(p for p in loc[:-1] if isinstance(p, str)))
        close = difflib.get_close_matches(name, list(section.model_fields) if section else [], n=1)
        return f"'{where}' is not a known key" + (f"; did you mean '{close[0]}'?" if close else ".")
    # Inside a list ("scoring.criteria[2].weight") the key alone is ambiguous, so the full path is shown.
    shown = where if any(isinstance(p, int) for p in loc) or not name else name
    example = next((_EXAMPLES[p] for p in reversed(loc) if isinstance(p, str) and p in _EXAMPLES), None)
    return f"'{shown}' {problem}" + (f", for example: {example}" if example else ".")


def _scoring_problems(cfg: ScoringConfig) -> list[tuple[tuple, str]]:
    """(location, message) for every scoring rule broken, in the order they are reported."""
    problems: list[tuple[tuple, str]] = []
    seen: set[str] = set()
    for i, c in enumerate(cfg.criteria):
        loc, name = ("scoring", "criteria", i), f"scoring.criteria[{i}]"
        if c.type not in CRITERION_TYPES:
            problems.append((loc + ("type",), f"{name} has type '{c.type}', which is not a known type. Known types: {', '.join(CRITERION_TYPES)}."))
        if c.id in seen:
            problems.append((loc + ("id",), f"{name} uses the id '{c.id}' a second time; every criterion needs its own id."))
        seen.add(c.id)
        if c.weight < 0:
            problems.append((loc + ("weight",), f"{name} ('{c.id}') has weight {c.weight}; weights cannot be negative."))
        if c.type == "tokens" and not c.tokens:
            problems.append((loc, f"{name} ('{c.id}') has type tokens but no tokens, for example: tokens: [\"logistic*\"]."))
        if not (0 <= c.nice_to_have_credit <= 1 and 0 <= c.near_ratio <= 1):
            problems.append((loc, f"{name} ('{c.id}'): nice_to_have_credit and near_ratio must be between 0 and 1."))
    for j, p in enumerate(cfg.penalties):
        loc, name = ("scoring", "penalties", j), f"scoring.penalties[{j}]"
        if p.code not in PENALTY_CODES:
            problems.append((loc + ("code",), f"{name} has code '{p.code}', which is not a known penalty. Known penalties: {', '.join(PENALTY_CODES)}."))
        for sub, points in [(("points",), p.points)] + [(("steps", k, "points"), s.points) for k, s in enumerate(p.steps)]:
            if points is not None and points > 0:
                problems.append((loc + sub, f"{name} ('{p.code}') has points {points}; penalty points must be 0 or below, for example -10."))
        if (p.code == "years_gap" and not p.steps) or (p.code == "hard_requirement_missing" and p.points is None):
            problems.append((loc, f"{name} ('{p.code}') needs {'steps' if p.code == 'years_gap' else 'points'}; see the commented honestcv.yml."))
    for layer in ("CORE", "STRETCH", "DOWNSHIFT"):
        if not 0 <= cfg.surface.min_match(layer) <= 100:  # type: ignore[arg-type]
            problems.append((("scoring", "surface", layer), f"scoring.surface.{layer}.min_match must be between 0 and 100."))
    if not 0 <= cfg.min_applicable_weight <= 100:
        problems.append((("scoring", "min_applicable_weight"), "scoring.min_applicable_weight must be between 0 and 100."))
    total = sum(c.weight for c in cfg.criteria)
    if total != 100:
        verb = "Lower" if total > 100 else "Raise"
        problems.append((("scoring", "criteria"), f"scoring.criteria weights sum to {total}; they must sum to 100. {verb} some weights by {abs(total - 100)} in total."))
    return problems


def validate_scoring(cfg: ScoringConfig) -> None:
    """Weights sum to 100, unique ids, known types, no negative weight, no penalty above 0. Raises ConfigError."""
    problems = _scoring_problems(cfg)
    if problems:
        raise ConfigError(problems[0][1], hint="Fix the scoring block in honestcv.yml, or delete it to use the defaults.")


# ---- loading ----


def _line_map(node: Any, prefix: tuple = (), out: dict | None = None) -> dict[tuple, int]:
    out = {} if out is None else out
    items = node.value if isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) else []
    for i, item in enumerate(items):
        key_node, value_node = item if isinstance(node, yaml.MappingNode) else (item, item)
        path = prefix + ((key_node.value,) if isinstance(node, yaml.MappingNode) else (i,))
        out[path] = key_node.start_mark.line + 1
        _line_map(value_node, path, out)
    return out


def _drop_nulls(value: Any) -> Any:
    """An empty key ("remote:" with nothing after it) means "use the default"."""
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    return [_drop_nulls(v) for v in value] if isinstance(value, list) else value


def load_config(path: Path | None) -> Config:
    """None or a missing file -> defaults. YAML and value errors raise ConfigError with the file and line."""
    if path is None or not Path(path).exists():
        return Config()
    shown = Path(path).name
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        node, data = yaml.compose(text, Loader=_Loader), yaml.load(text, Loader=_Loader)
    except UnicodeDecodeError:
        raise ConfigError(f"{shown} is not UTF-8 text.", hint="Save it again as UTF-8 (plain text).", path=shown) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        hint = "Replace the tab characters with spaces." if "\t" in text else "Check the indentation: use spaces and put a space after each colon."
        raise ConfigError(f"{shown} could not be read ({getattr(exc, 'problem', None) or 'not valid YAML'}).", hint=hint,
                          path=shown, line=mark.line + 1 if mark else None) from None  # fmt: skip
    if not isinstance(data or {}, dict):
        raise ConfigError(f"{shown} must hold keys such as profile: and preferences:.", hint="Start from the file honestcv init writes.", path=shown, line=1)
    lines = _line_map(node) if node is not None else {}

    def line_for(loc: tuple) -> int | None:
        return next((lines[loc[:n]] for n in range(len(loc), 0, -1) if loc[:n] in lines), None)

    try:
        cfg = Config.model_validate(_drop_nulls(data or {}))
    except ValidationError as exc:
        messages = validation_messages(exc)
        loc, message = messages[0][0], _friendly(*messages[0])
        if len(messages) > 1:
            message += f" ({len(messages) - 1} more problem{'s' if len(messages) > 2 else ''} in this file.)"
        problems = [(loc, message)]
    else:
        problems = _scoring_problems(cfg.scoring)[:1]
    if problems:
        loc, message = problems[0]
        line = line_for(loc)
        hint = f"Edit {shown}" + (f" line {line}" if line else "") + ", or delete the line to use the default."
        raise ConfigError(message, hint=hint, path=shown, line=line)
    return cfg


def config_digest(cfg: Config) -> str:
    """sha256 of the merged config as canonical JSON."""
    payload = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
