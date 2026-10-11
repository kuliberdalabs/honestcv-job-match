"""Claims ledger: load with line numbers, lint, draft from CVs, and the index scoring reads."""

import datetime as dt
import json
import re
import weakref
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from honestcv.errors import LedgerError, format_loc, validation_messages
from honestcv.models import Ledger, LedgerIndex, LintIssue, Profile
from honestcv.textutil import normalize, safe_name, sentences, tokenize

ACCEPTED_EVIDENCE = {
    "built": ("repo", "file", "url", "note"), "tested": ("test_record",), "shipped": ("handover_record",),
    "used": ("usage_evidence",), "acknowledged": ("recipient_statement",), "paid": ("invoice_or_transfer",),
    "generated": ("file_list", "file"), "non_cash_value": ("note",),
}  # fmt: skip
_LINES: dict[int, tuple["weakref.ref[Ledger]", dict[str, int]]] = {}  # id(ledger) -> (ledger, field path -> line)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+\S")
_LAST_PART = re.compile(r"(?:\[\d+\]|\.[^.\[]+)$")


class YamlLoader(yaml.SafeLoader):
    """SafeLoader where only true/false are booleans, so a not_confirmed item like "no" stays text."""


YamlLoader.yaml_implicit_resolvers = {
    key: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
YamlLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def _line_map(node: yaml.Node | None, prefix: tuple = (), out: dict[str, int] | None = None) -> dict[str, int]:
    out = {} if out is None else out
    pairs = node.value if isinstance(node, yaml.MappingNode) else enumerate(node.value) if isinstance(node, yaml.SequenceNode) else []
    for key, value in pairs:
        path = prefix + ((key.value,) if isinstance(node, yaml.MappingNode) else (key,))
        out.setdefault(format_loc(path), (key if isinstance(node, yaml.MappingNode) else value).start_mark.line + 1)
        _line_map(value, path, out)
    return out


def _line_for(lines: dict[str, int], path: str) -> int | None:
    while path in lines or _LAST_PART.search(path):
        if path in lines:
            return lines[path]
        path = _LAST_PART.sub("", path)
    return None


def load_ledger(path: Path) -> Ledger | None:
    """The ledger in path, or None if the file is missing or parses to zero claims. YAML and schema errors raise
    LedgerError with the file and line; the message never repeats the value from the file."""
    path = Path(path)
    if not path.is_file():
        return None
    name = path.name
    try:
        text = path.read_text(encoding="utf-8-sig")
        node, data = yaml.compose(text, Loader=YamlLoader), yaml.load(text, Loader=YamlLoader)
    except UnicodeDecodeError:
        raise LedgerError(f"{name} is not UTF-8 text.", hint="Save it again as UTF-8 (plain text).", path=name) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise LedgerError(f"{name} could not be read ({getattr(exc, 'problem', None) or 'not valid YAML'}).", path=name,
                          hint="Check the indentation: use spaces and put a space after each colon.",
                          line=mark.line + 1 if mark else None) from None  # fmt: skip
    if data is None:
        return None
    if not isinstance(data, dict):
        raise LedgerError(f"{name} must hold keys such as schema_version: and claims:.", path=name, line=1,
                          hint="Start from the file honestcv init writes, or run: honestcv ledger draft")  # fmt: skip
    if not data.get("claims"):
        return None
    lines = _line_map(node)
    try:
        ledger = Ledger.model_validate(data)
    except ValidationError as exc:
        loc, problem = validation_messages(exc)[0]
        where = format_loc(loc)
        line = _line_for(lines, where)
        raise LedgerError(f"{where} {problem}.", path=name, line=line,
                          hint=f"Edit {name}" + (f" line {line}" if line else "") + ". The ledger schema is in docs/ledger.md.") from None  # fmt: skip
    for key in [k for k, (ref, _) in _LINES.items() if ref() is None]:
        del _LINES[key]
    _LINES[id(ledger)] = (weakref.ref(ledger), lines)
    return ledger


def _compile_error(pattern: str) -> str | None:
    try:
        re.compile(pattern)
    except re.error as exc:
        return str(exc)
    return None


def _ref_problem(kind: str, ref: str, root: Path) -> str | None:
    """A path ref must exist under the workspace; a URL must parse (it is never fetched); free text only for note."""
    ref = ref.strip()
    if kind == "note":
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*://", ref, re.IGNORECASE):
        parts = urlparse(ref)
        return None if parts.scheme and parts.netloc else f"'{ref}' is not a valid URL"
    target = (root / ref).resolve()
    if not target.is_relative_to(root):
        return f"evidence path {ref} points outside the workspace"
    if target.exists():
        return None
    if " " in ref:
        return f"free text is accepted only for type note; for type {kind} give a file path in the workspace or a URL"
    return f"evidence file {ref} does not exist in the workspace"


def lint_ledger(ledger: Ledger, ws_root: Path, today: dt.date) -> list[LintIssue]:
    """ERROR and WARN lines (docs/ledger.md), in ledger order, with field paths and (for a loaded ledger) line numbers."""
    from honestcv.checker import guard_allowed  # the checker imports this module

    entry = _LINES.get(id(ledger))
    lines = entry[1] if entry and entry[0]() is ledger else {}
    issues: list[LintIssue] = []

    def add(severity: str, path: str, message: str) -> None:
        issues.append(LintIssue(severity=severity, path=path, line=_line_for(lines, path), message=message))

    as_of, days = ledger.person.get("as_of"), ledger.person.get("stale_after_days", 365)
    try:
        as_of = dt.date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    except ValueError:
        add("ERROR", "person.as_of", "person.as_of must be a date like 2026-09-01")
    if isinstance(as_of, dt.date) and isinstance(days, int) and as_of + dt.timedelta(days=days) < today:
        add("WARN", "person.as_of", f"person.as_of {as_of} is older than stale_after_days ({days}); review the ledger, then update as_of")
    for j, fp in enumerate(ledger.rules.forbidden_phrases):
        if err := _compile_error(fp.pattern):
            add("ERROR", f"rules.forbidden_phrases[{j}]", f"invalid regular expression ({err})")
    guards: dict[int, list[tuple[int, str, str, str]]] = {}
    for i, j, rule, severity, message in guard_allowed(ledger, today):
        guards.setdefault(i, []).append((j, rule, severity, message))
    private = {t for c in ledger.claims for n in c.private_names for t in tokenize(n) if len(t) >= 3}
    root, seen = Path(ws_root).resolve(), set()
    for i, c in enumerate(ledger.claims):
        p = f"claims[{i}]"
        if c.id in seen:
            add("ERROR", f"{p}.id", f"duplicate claim id '{c.id}'; every id must be unique")
        seen.add(c.id)
        if leaked := sorted(t for t in private if t in c.id.lower()):
            add("ERROR", f"{p}.id", f"claim id '{c.id}' contains '{leaked[0]}' from private_names; ids appear in tailored files, so rename it")
        if c.kind == "ceiling" and not c.statement.strip():
            add("ERROR", f"{p}.statement", f"{c.id}: a ceiling claim needs a statement")
        for state, evidence in c.confirmed.items():
            accepted = ACCEPTED_EVIDENCE[state]
            if not any(e.type in accepted for e in evidence):
                add("ERROR", f"{p}.confirmed.{state}", f"{c.id}: state '{state}' needs evidence of type {' or '.join(accepted)}")
            for j, e in enumerate(evidence):
                if problem := _ref_problem(e.type, e.ref, root):
                    add("ERROR", f"{p}.confirmed.{state}[{j}].ref", f"{c.id}: {problem}")
        for fld in ("forbidden_here", "blocks_requirements"):
            for j, pattern in enumerate(getattr(c, fld)):
                if err := _compile_error(pattern):
                    add("ERROR", f"{p}.{fld}[{j}]", f"{c.id}: invalid regular expression ({err})")
        if c.review_by and c.review_by < today:
            add("WARN", f"{p}.review_by", f"{c.id}: review_by {c.review_by} has passed")
        if c.draft:
            add("WARN", f"{p}.draft", f"{c.id}: draft: your own CV line, not yet backed by evidence")
        elif not c.allowed and c.visibility != "never":
            add("WARN", f"{p}.allowed", f"{c.id}: no allowed sentence, so tailoring cannot use it")
        elif c.kind == "ceiling":
            for j, sentence in enumerate(c.allowed):
                for k, pattern in enumerate(c.forbidden_here):
                    if not _compile_error(pattern) and re.search(pattern, sentence):
                        add("ERROR", f"{p}.allowed[{j}]", f"{c.id}: this sentence matches its own forbidden_here[{k}]")
                        break
        for j, rule, severity, message in guards.get(i, []):
            error = severity == "BLOCK" or rule == "MISSING_QUALIFIER"
            add("ERROR" if error else "WARN", f"{p}.allowed[{j}]", f"{c.id}: {rule}: {message}")
    return issues


def build_index(ledger: Ledger | None) -> LedgerIndex | None:
    """Skill backing (non-draft claims with any confirmed state), draft skills, and ceiling patterns."""
    if ledger is None:
        return None
    from honestcv.checker import canonical_skill

    backing: dict[str, set[str]] = {}
    drafts: dict[str, set[str]] = {}
    ceilings: list[tuple[str, str]] = []
    for c in ledger.claims:
        if c.kind == "ceiling":
            ceilings += [(c.id, p) for p in c.blocks_requirements if not _compile_error(p)]
            continue
        target = drafts if c.draft else backing if c.confirmed else None
        for skill in c.skills if target is not None else []:
            target.setdefault(canonical_skill(skill), set()).add(c.id)
    return LedgerIndex(skill_backing={k: sorted(v) for k, v in sorted(backing.items())},
                       draft_skills={k: sorted(v) for k, v in sorted(drafts.items())}, ceiling_patterns=ceilings)  # fmt: skip


# ---------------------------------------------------------------- draft

_DRAFT_HEAD = """\
# honestcv claims ledger, drafted from your CVs on {today}.
# Every claim below is marked draft: true. A draft claim does not count as ledger backing yet: tailoring and
# the check accept its sentence only word for word, as it stands in your CV.
# To confirm a claim:
#   1. under confirmed, add each state you can prove, with its evidence. The ladder is
#      built < tested < shipped < used < acknowledged < paid; a sentence may not claim a state above it.
#   2. evidence types: built repo, file, url or note; tested test_record; shipped handover_record;
#      used usage_evidence; acknowledged recipient_statement; paid invoice_or_transfer.
#      A file path is relative to this folder (put files in evidence/).
#   3. write the sentence you want to use under allowed, then delete the line "draft: true".
# Then run: honestcv ledger lint
schema_version: 1
person:
  as_of: {today}
  stale_after_days: 365
"""
_CONFIRM_HINT = {
    "delivery": ["# To confirm: add the states you can prove under confirmed, for example",
                 "#   built: [{type: file, ref: evidence/your-file.txt, date: 2024-02-12}]",
                 "#   shipped: [{type: handover_record, ref: evidence/handover-note.txt}]",
                 '# then delete "draft: true".'],
    "biography": ["# To confirm: check the years (facts: start_year, end_year), then delete \"draft: true\"."],
}  # fmt: skip


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # a JSON string is a valid double-quoted YAML scalar


def _slug(text: str, taken: set[str]) -> str:
    words = [w for w in tokenize(text) if not w.isdigit()][:5]
    base = safe_name(" ".join(words) or text)[:48].rstrip("-") or "claim"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    taken.add(slug)
    return slug


def _cv_lines(cv_id: str, text: str, profile: Profile | None) -> list[tuple[str, str, int | None, list[int]]]:
    """(kind, sentence, line number, years) for each bullet and experience line of one CV, in text order."""
    lines = normalize(text).split("\n")
    experience: dict[int, list[int]] = {}
    extra = []
    for title, dates in profile.experience if profile else []:
        years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", dates)]
        no = next((n for n, raw in enumerate(lines, 1) if title.lower() in raw.lower() and all(str(y) in raw for y in years)), None)
        if no is None:
            extra.append(("biography", f"{title}, {dates}", None, years))
        else:
            experience.setdefault(no, years)
    out = []
    for no, raw in enumerate(lines, 1):
        if no in experience or _BULLET.match(raw):
            unit = sentences("- " + raw.strip().lstrip("#").strip() if no in experience else raw.strip())
            if unit:
                out.append(("biography" if no in experience else "delivery", unit[0][1], no, experience.get(no, [])))
    return out + extra


def draft_ledger(profiles: list[Profile], cv_texts: dict[str, str], *, today: dt.date) -> str:
    """YAML text of a starting ledger: one draft claim per distinct CV bullet or experience line, with comments.
    Identical lines from several CVs become one claim. It lints with one WARN per draft claim and no ERROR."""
    from honestcv.checker import skill_terms

    by_cv = {p.cv_id: p for p in profiles}
    out = [_DRAFT_HEAD.format(today=today.isoformat())]
    seen: set[str] = set()
    taken: set[str] = set()
    claims = []
    for cv_id in sorted(cv_texts):
        for kind, sentence, no, years in _cv_lines(cv_id, cv_texts[cv_id], by_cv.get(cv_id)):
            key = " ".join(sentence.lower().split()).rstrip(".")
            if key in seen:
                continue
            seen.add(key)
            skills = sorted(dict.fromkeys(name for name, s, e in skill_terms(sentence)))
            source = f"cvs/{cv_id}" + (f"#line {no}" if no else "")
            claim = [f"  - id: {_q(_slug(sentence, taken))}", f"    kind: {kind}", f"    summary: {_q(sentence)}",
                     f"    skills: [{', '.join(_q(s) for s in skills)}]", "    confirmed: {}", "    allowed:",
                     f"      - {_q(sentence)}", "    draft: true", f"    draft_source: {_q(source)}"]  # fmt: skip
            if kind == "biography" and len(years) == 2:
                claim.append(f"    facts: {{start_year: {years[0]}, end_year: {years[1]}, precision: year}}")
            claims.append("\n".join(claim + ["    " + h for h in _CONFIRM_HINT[kind]]))
    out.append("claims:" if claims else "claims: []")
    return "\n".join(out) + ("\n" + "\n\n".join(claims) if claims else "") + "\n"
