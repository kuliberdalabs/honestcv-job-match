"""CV text loading (md, txt, pdf, docx), the skills reader and profile extraction.

The skills reader lives here because a CV and an offer are read with the same rules; scoring.py imports it.
"""

import codecs
import datetime as dt
import functools
import hashlib
import itertools
import json
import re
import unicodedata
import zipfile
from collections import Counter
from importlib import resources
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml
from pypdf import PdfReader

from honestcv import __version__
from honestcv.config import Config, ProfileOverrides
from honestcv.errors import WorkspaceError
from honestcv.models import CvText, Profile
from honestcv.textutil import _token_regex, durations, hit, normalize
from honestcv.workspace import Workspace, atomic_write

__all__ = ["CvText", "canonical", "extract_profile", "known_skill", "load_cv_text", "load_profiles", "pack", "scan_skills", "skill_tokens"]

SUPPORTED = (".md", ".txt", ".pdf", ".docx")
LEGACY = (".doc", ".odt", ".pages", ".rtf", ".webarchive")
BAD_LETTERS = "Polish letters in this PDF were read wrongly. Export the CV as DOCX or a text-based PDF."


@functools.cache
def pack(name: str) -> dict:
    """A data pack from honestcv/data/ (YAML). Every value in the packs is text, a list of text or a list of lists."""
    return yaml.safe_load(resources.files("honestcv").joinpath(f"data/{name}").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- reading a CV file


def _refuse(path: Path, message: str, hint: str) -> WorkspaceError:
    return WorkspaceError(message, hint=hint, path=path.name)


def _decode(raw: bytes) -> tuple[str, list[str]]:
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace"), []
    try:
        return raw.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        note = "The file is not UTF-8; it was read as Windows-1250. If letters look wrong, save it as UTF-8."
        return raw.decode("cp1250", errors="replace"), [note]


def _read_pdf(path: Path) -> tuple[str, int]:
    try:
        reader = PdfReader(str(path))
    except Exception:
        raise _refuse(path, f"{path.name} could not be opened as a PDF.", "Export it again as PDF or DOCX.") from None
    try:
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("password needed")
    except Exception:
        raise _refuse(path, "This PDF is protected.", "Export it again without protection, or as DOCX.") from None
    try:
        return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)
    except Exception:
        raise _refuse(path, f"{path.name} could not be read.", "Export it again as PDF or DOCX.") from None


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def _paragraph(p: ET.Element) -> str:
    parts: list[str] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag in (_FALLBACK, _W + "txbxContent"):  # text box paragraphs are read once, by _paragraphs
                continue
            if child.tag == _W + "t":
                parts.append(child.text or "")
            elif child.tag == _W + "tab":
                parts.append("\t")
            elif child.tag in (_W + "br", _W + "cr"):
                parts.append("\n")
            else:
                walk(child)

    walk(p)
    text = "".join(parts)
    style = p.find(f"{_W}pPr/{_W}pStyle")
    heading = re.fullmatch(r"Heading(\d)", style.get(_W + "val", "")) if style is not None else None
    if heading and text.strip():
        return "#" * (int(heading.group(1)) + 1) + " " + text
    return "- " + text if p.find(f"{_W}pPr/{_W}numPr") is not None and text.strip() else text


def _paragraphs(el: ET.Element, out: list[str]) -> None:
    for child in el:
        if child.tag == _FALLBACK:  # the drawing fallback repeats the text of mc:Choice
            continue
        if child.tag == _W + "p":
            out.append(_paragraph(child))
        _paragraphs(child, out)


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        raise _refuse(path, f"{path.name} could not be opened as a DOCX.", "Save it again as DOCX or PDF.") from None
    lines: list[str] = []
    _paragraphs(root, lines)
    return "\n".join(lines)


def load_cv_text(path: Path) -> CvText:
    """.md .txt .pdf (pypdf) .docx (zipfile + xml, mc:Fallback skipped); normalize() is applied. Files that cannot
    be used, an empty one included, raise WorkspaceError with a message that says how to fix it. Bad decoding of a
    PDF goes to warnings."""
    path = Path(path).expanduser()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise _refuse(path, f"{path.name} is a {suffix or 'unknown'} file, which honestcv cannot read.", "Save it as PDF or DOCX, then try again.")
    if not path.is_file():
        raise _refuse(path, f"The CV file {path.name} was not found.", "Check the file name; put it in quotes if it has spaces.")
    warnings: list[str] = []
    pages: int | None = None
    if suffix == ".pdf":
        raw, pages = _read_pdf(path)
    elif suffix == ".docx":
        raw = _read_docx(path)
    else:
        raw, warnings = _decode(path.read_bytes())
    text = normalize(raw)
    if not text.strip():
        if suffix == ".pdf":
            raise _refuse(path, "This PDF has no text layer (it is an image).", "Export your CV as text-based PDF or DOCX.")
        raise _refuse(path, f"{path.name} has no readable text.", "Put your CV text in the file and save it, or add a DOCX or text-based PDF instead.")
    if suffix == ".pdf":
        bad = text.count("\ufffd") + sum(unicodedata.category(c) in ("Mn", "Mc", "Me") for c in text)
        if bad > 0.01 * max(1, sum(c.isalpha() for c in text)):
            warnings.append(BAD_LETTERS)
    return CvText(text=text, pages=pages, warnings=warnings)


# ---------------------------------------------------------------- the skills reader (CVs and offers)


@functools.cache
def _skills() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """name -> (tokens counted in free text, every token). An ambiguous name counts in free text only through
    its aliases."""
    table = {}
    for e in pack("skills.yml")["skills"]:
        aliases = tuple(e.get("aliases", ()))
        table[e["name"]] = (aliases if e.get("ambiguous") else (e["name"], *aliases), (e["name"], *aliases))
    return table


@functools.cache
def _alias_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, (_, every) in _skills().items():
        for token in every:
            if not token.endswith("*"):
                out.setdefault(token.lower(), name)
    return out


def canonical(term: str) -> str:
    """The canonical skill name for a name or alias ("k8s" -> "kubernetes"); an unknown term stays, lower case."""
    t = " ".join(str(term).lower().split())
    return _alias_map().get(t, t)


def known_skill(name: str) -> bool:
    """True when the name is a canonical skill of skills.yml."""
    return name in _skills()


def skill_tokens(name: str) -> tuple[str, ...]:
    """Every token (the name and its aliases) that finds this skill in a text."""
    return _skills().get(name, ((), (name,)))[1]


def _sentence_start(low: str, pos: int) -> int:
    lo = max(0, pos - 40)  # a sentence start is never more than 40 characters back, so only that window is searched
    k = low.rfind(". ", max(0, lo - 1), pos)
    return max(low.rfind("\n", lo, pos) + 1, k + 2 if k >= 0 else 0, lo)


@functools.cache
def _negation_rx() -> tuple["re.Pattern[str]", "re.Pattern[str]"]:
    """(cues before a mention, cues after it), each list as one pattern that finds what hit() finds for any cue."""
    cues = pack("skills.yml")
    patterns = ([rx.pattern for n in cues[key] if (rx := _token_regex(n))] for key in ("negations_before", "negations_after"))
    before, after = (re.compile("|".join(p) or "(?!)") for p in patterns)  # (?!) matches nothing
    return before, after


def _negated(low: str, s: int, e: int) -> bool:
    after = e + 40
    for sep in ("\n", ". "):
        k = low.find(sep, e, e + 40 + len(sep))
        after = min(after, k) if k >= 0 else after
    pieces = (low[_sentence_start(low, s) : s].lower(), low[e:after].lower())
    return any(rx.search(text) for rx, text in zip(_negation_rx(), pieces))


def _first_word(token: str) -> str:
    """The first word of a token as _token_regex() reads it: lower case, without a trailing prefix star."""
    t = " ".join(token.lower().split())
    return (t[:-1] if t.endswith("*") and len(t) > 1 else t).split(" ")[0]


@functools.lru_cache(maxsize=2048)
def scan_skills(low: str, direct: bool = False) -> tuple[tuple[str, int, int], ...]:
    """(skill, start, end) for every skill mention in lower-cased text, in text order; negated mentions are dropped.
    direct=True (a CV skills line) also counts the bare name of an ambiguous skill."""
    found = set()
    for name, (free, every) in _skills().items():
        for token in dict.fromkeys(every if direct else free):
            if _first_word(token) not in low:  # the pattern cannot match a text that lacks the token's first word
                continue
            found.update((name, m.start(), m.end()) for m in _token_regex(token).finditer(low) if not _negated(low, m.start(), m.end()))
    # "looker studio" is one mention of one skill, not also a mention of "looker": a mention inside a longer mention
    # of another skill is dropped. One pass in text order; best/second are the furthest-reaching ends seen so far
    # for two different skills, so the longer mention that holds a mention is found without comparing all pairs.
    inside = set()
    best = second = (-1, "")  # (end, skill)
    ordered = sorted(found, key=lambda h: (h[1], -h[2]))
    for _, group in itertools.groupby(ordered, key=lambda h: h[1:]):
        group = list(group)  # mentions with the same span never hold each other
        for h in group:
            if (second if best[1] == h[0] else best)[0] >= h[2]:
                inside.add(h)
        for name, _, end in group:
            if name == best[1]:
                best = max(best, (end, name))
            elif name == second[1]:
                second = max(second, (end, name))
            elif end > best[0]:
                best, second = (end, name), best
            elif end > second[0]:
                second = (end, name)
            if second[0] > best[0]:
                best, second = second, best
    return tuple(sorted(found - inside, key=lambda h: (h[1], h[2], h[0])))


# ---------------------------------------------------------------- CV sections, titles, years, languages


@functools.cache
def _heading_names() -> dict[str, str]:
    return {re.sub(r"[^\w\s]", "", n).lower().strip(): kind for kind, names in pack("skills.yml")["cv_headings"].items() for n in names}


def _heading(line: str) -> tuple[int, str | None] | None:
    """(markdown level or 0, section kind or None) for a heading line, else None."""
    m = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", line.strip())
    text, level = (m.group(2), len(m.group(1))) if m else (line.strip().strip("*_: "), 0)
    kind = _heading_names().get(re.sub(r"[^\w\s]", "", text).lower().strip()) if len(text) <= 40 else None
    return (level, kind) if m or kind else None


def sections(text: str) -> list[tuple[str, list[str]]]:
    """(kind, lines) per CV section; kind is experience, education, skills, languages or other. A deeper markdown
    heading that names no section ("### Job title, Company, 2020-2026") stays inside the section it sits in."""
    out: list[list] = []  # [kind, level, lines]
    for line in text.split("\n"):
        h = _heading(line)
        if h and (h[1] or not out or 0 < h[0] <= out[-1][1]):
            out.append([h[1] or "other", h[0] or 2, []])
        else:
            if not out:
                out.append(["other", 1, []])
            out[-1][2].append(line)
    return [(kind, lines) for kind, _, lines in out]


_BULLET = re.compile(r"^\s*(?:[-*+\u2022\u25e6\u25aa\u2023\u2013\u2014]|\d{1,3}[.)])\s+")
_SPLIT = re.compile(r"\s*[,;|]\s*|\s+[-\u2013\u2014@]\s+|\s+(?:at|w)\s+", re.IGNORECASE)


def _title(text: str) -> str | None:
    text = re.sub(r"[(\[]\s*[)\]]", "", text).strip(" #*_|-\u2013\u2014:\t")
    text = _SPLIT.split(text, maxsplit=1)[0].strip(" #*_|-\u2013\u2014:,;()\t")
    return text if text and len(text.split()) <= 8 and any(c.isalpha() for c in text) else None


def _experience(secs: list[tuple[str, list[str]]]) -> tuple[list[list[str]], list[list[int | None]]]:
    """Job titles with their period, and the date ranges, found under experience headings only."""
    jobs: list[list[str]] = []
    ranges: list[list[int | None]] = []
    for kind, lines in secs:
        previous = None
        for line in (ln for ln in lines if kind == "experience" and ln.strip() and not _BULLET.match(ln)):
            dated = [d for d in durations(line) if d.start_year]
            if not dated:
                previous = line
                continue
            d = dated[0]
            title = _title(line[: d.start] + line[d.start + len(d.raw) :]) or (_title(previous) if previous else None)
            ranges.append([d.start_year, d.end_year])
            if title:
                jobs.append([title, f"{d.start_year}-{d.end_year or 'present'}"])
            previous = None
    return jobs, ranges


def _years(ranges: list[list[int | None]], today: dt.date) -> float | None:
    """Years covered by the ranges, overlaps merged; an open range runs to this year."""
    spans = sorted((a, b or today.year) for a, b in ranges if a and a <= today.year and (b or today.year) >= a)
    total, end = 0, None
    for a, b in spans:
        if end is None or a > end:
            total, end = total + (b - a), b
        elif b > end:
            total, end = total + (b - end), b
    return float(total) if spans else None


def _language_level(line: str, s: int, e: int) -> str | None:
    window = re.split(r"[,;|]", line[max(0, s - 20) : s])[-1] + line[s:e] + re.split(r"[,;|]", line[e : e + 30])[0]
    cefr = re.search(r"\b([abc][12])\b", window)
    if cefr:
        return cefr.group(1).upper()
    return next((label for label, words in pack("languages.yml")["level_words"].items() if any(hit(window, w) for w in words)), None)


def _languages(secs: list[tuple[str, list[str]]]) -> dict[str, str]:
    """Languages named in a languages section, or anywhere next to a level (C1, native, fluent ...)."""
    cfg, out = pack("languages.yml"), {}
    for kind, lines in secs:
        for low in (ln.lower() for ln in lines):
            for code, names in cfg["names"].items():
                for name in names:
                    for m in _token_regex(name).finditer(low):
                        follow = re.match(r"\s+(\w+)", low[m.end() :])
                        level = _language_level(low, m.start(), m.end())
                        if (follow and follow.group(1) in cfg["determiners"]) or not (level or kind == "languages"):
                            continue
                        if out.get(code, "listed") == "listed":
                            out[code] = level or "listed"
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- profiles


def _analyze(path: Path) -> dict:
    """What is read from the CV file itself, before honestcv.yml overrides. JSON-able; cached by file hash.
    Skills come most prominent first: most mentions, then earliest."""
    cv = load_cv_text(path)
    secs = sections(cv.text)
    labels = "|".join(re.escape(x) for x in pack("skills.yml")["skill_labels"])
    lines = "\n".join(ln for kind, ls in secs for ln in ls if kind == "skills" or re.match(rf"^\W*(?:{labels})\s*:", ln, re.IGNORECASE))
    low = cv.text.lower()
    count: Counter[str] = Counter()
    first: dict[str, int] = {}
    for skill, start, _ in scan_skills(low):
        count[skill] += 1
        first.setdefault(skill, start)
    for skill, start, _ in scan_skills(lines.lower(), True):  # an ambiguous name is counted only on a skills line
        if skill not in count:
            count[skill], first[skill] = 1, len(low) + start
    jobs, ranges = _experience(secs)
    return {"skills": sorted(count, key=lambda k: (-count[k], first[k], k)), "titles": list(dict.fromkeys(t for t, _ in jobs)), "experience": jobs, "ranges": ranges,
            "languages": _languages(secs), "pages": cv.pages, "warnings": cv.warnings}  # fmt: skip


def _assemble(cv_id: str, sha: str, base: dict, ov: ProfileOverrides, today: dt.date) -> Profile:
    removed = {canonical(s) for s in ov.skills_remove}
    skills = [k for k in dict.fromkeys([*(canonical(s) for s in ov.skills_add), *base["skills"]]) if k not in removed]
    years = ov.years if ov.years is not None else _years(base["ranges"], today)
    return Profile(cv_id=cv_id, cv_sha256=sha, skills=skills, titles=base["titles"], target_titles=ov.target_titles or base["titles"],
                   years=years, level=ov.level, languages=base["languages"], experience=[tuple(j) for j in base["experience"]],
                   pages=base["pages"], warnings=base["warnings"])  # fmt: skip


def extract_profile(path: Path, overrides: ProfileOverrides, *, today: dt.date | None = None) -> Profile:
    """Skills, job titles, years and languages of one CV. The level is never guessed: it is profile.level or None.
    today (default: the real date) matters only for a date range that is still open."""
    path = Path(path).expanduser()
    base = _analyze(path)
    return _assemble(path.name, hashlib.sha256(path.read_bytes()).hexdigest(), base, overrides, today or dt.date.today())


def _cached(ws: Workspace, sha: str) -> dict | None:
    try:
        stored = json.loads((ws.state_dir / "profiles" / f"{sha}.json").read_text(encoding="utf-8"))
        return stored["base"] if stored.get("version") == __version__ else None
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def load_profiles(ws: Workspace, cfg: Config, *, today: dt.date | None = None, cache: bool = True) -> list[Profile]:
    """One Profile per CV file in cvs/, sorted by file name. The part read from the file is cached in
    .honestcv/profiles/<sha256>.json; the honestcv.yml overrides are applied on every call. cache=False reads
    and writes nothing (for doctor)."""
    files = [p for p in ws.cvs.iterdir() if p.is_file() and not p.name.startswith((".", "~$"))] if ws.cvs.is_dir() else []
    out: list[Profile] = []
    for path in sorted(files, key=lambda p: (p.name.casefold(), p.name)):
        if path.suffix.lower() not in SUPPORTED + LEGACY:
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        base = _cached(ws, sha) if cache else None
        if base is None:
            base = _analyze(path)
            if cache:
                stored = json.dumps({"version": __version__, "base": base}, ensure_ascii=False)
                atomic_write(ws.state_dir / "profiles" / f"{sha}.json", stored)
        out.append(_assemble(path.name, sha, base, cfg.profile, today or dt.date.today()))
    return out
