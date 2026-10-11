"""Text helpers shared by every module: normalization, token matching, numbers, durations, sentences, safe names.

All matching is case-insensitive and Unicode-aware, so Polish letters count as word characters.
"""

import functools
import html
import re
import unicodedata
from html.parser import HTMLParser
from importlib import resources
from pathlib import Path

from honestcv.models import DurationMention, NumberMention

# ---------------------------------------------------------------- normalize, plain

_DROP = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"), None)
_STRAIGHT = str.maketrans("\u2018\u2019\u201a\u201b\u2032\u201c\u201d\u201e\u201f\u2033\u00ab\u00bb\u2010\u2011", "'''''\"\"\"\"\"\"\"--")
_LINE_END_HYPHEN = re.compile(r"([^\W\d_])-[ \t]*\n[ \t]*([^\W\d_])")


def normalize(text: str) -> str:
    """NFKC (ligatures, decomposed Polish letters), drop soft hyphens and zero-width marks, straight quotes,
    \\n line ends, and join a word hyphenated across lines when the next line goes on in lower case."""
    text = unicodedata.normalize("NFKC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = text.translate(_DROP).translate(_STRAIGHT)
    return _LINE_END_HYPHEN.sub(lambda m: m.group(1) + m.group(2) if m.group(2).islower() else m.group(0), text)


_BLOCK_TAGS = set("address article aside blockquote br dd div dl dt footer form h1 h2 h3 h4 h5 h6 header hr li main "
                  "nav ol p pre section table tbody td th thead tr ul".split())  # fmt: skip
_SKIP_TAGS = {"script", "style", "head", "noscript", "template", "svg", "iframe", "object"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n- " if tag == "li" else " " if tag in ("td", "th") else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK_TAGS and tag not in ("td", "th"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(re.sub(r"\s+", " ", data))


def plain(html_or_text: str) -> str:
    """HTML to plain text (blocks become line breaks, list items "- " bullets), then normalize().
    Text without tags is only unescaped; it keeps at most one blank line in a row."""
    is_html = bool(re.search(r"<[A-Za-z!/]", html_or_text))
    if is_html:
        parser = _TextParser()
        parser.feed(html_or_text)
        parser.close()
        text = "".join(parser.parts)
    else:
        text = html.unescape(html_or_text)
    text = "\n".join(" ".join(line.split()) for line in text.replace("\r\n", "\n").split("\n"))
    text = re.sub(r"\n{2,}", "\n", text) if is_html else re.sub(r"\n{3,}", "\n\n", text)
    return normalize(text.strip())


# ---------------------------------------------------------------- hit, tokens, stopwords, stem


@functools.lru_cache(maxsize=4096)
def _token_regex(token: str) -> "re.Pattern[str] | None":
    t = " ".join(token.lower().split())
    if not t:
        return None
    if t.endswith("*") and len(t) > 1:
        return re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, t[:-1].split())))
    body = r"\s+".join(map(re.escape, t.split()))
    if not t[0].isalnum():
        return re.compile(body)
    return re.compile(r"(?<!\w)" + body + (r"(?!\w)" if t[-1].isalnum() else ""))


def hit(text_lower: str, token: str) -> bool:
    """"auto*" = prefix at a word start; a bare token = whole word or phrase; a token that starts with a
    non-alphanumeric character (".net") = substring. Case-insensitive."""
    rx = _token_regex(token)
    return bool(rx and rx.search(text_lower.lower()))


@functools.lru_cache(maxsize=4)
def stopwords(lang: str | None = None) -> frozenset[str]:
    """Stopwords from data/stopwords.txt: "en", "pl", or both for None (or an unknown language)."""
    sections: dict[str, set[str]] = {"en": set(), "pl": set()}
    current = "en"
    for raw in resources.files("honestcv").joinpath("data/stopwords.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip().lower()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, set())
        elif line and not line.startswith("#"):
            sections[current].add(line)
    return frozenset(sections[lang]) if lang in sections else frozenset().union(*sections.values())


_WORD = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*")


def tokenize(text: str, lang: str | None = None) -> list[str]:
    """Lower-cased word tokens in text order, split on whitespace and punctuation (hyphens included),
    possessive 's dropped, stopwords removed. Numbers stay; tokens are not stemmed."""
    stop, out = stopwords(lang), []
    for m in _WORD.finditer(text.lower()):
        word = m.group(0).replace("\u2019", "'")
        word = (word[:-2] if word.endswith("'s") else word).replace("'", "")
        if word and word not in stop:
            out.append(word)
    return out


_PL_CHARS = frozenset("ąćęłńóśźż")
_PL_MARKERS = ("ami", "ych", "ymi", "imi", "owi", "owie", "ego", "emu", "cja", "cji", "cje", "sja", "sji", "zja", "zji")
_PL_ENDINGS = tuple(sorted(set("ościami ościach owego owemu owych owymi ością ości ość ami ach ych ich ymi imi ego emu "
                               "owi owie iem ów om em ie ia ii ji ją ję ja je y i a u e o ę ą".split()), key=lambda e: (-len(e), e)))  # fmt: skip
_EN_ACH = frozenset("approach attach beach breach coach detach each outreach reach spinach stomach teach".split())
_VOWELS = frozenset("aeiouy")


def _looks_polish(t: str) -> bool:
    if any(c in _PL_CHARS for c in t):
        return True
    if t.endswith("ach"):
        return len(t) > 5 and t not in _EN_ACH and t[-4] not in _VOWELS
    return t.endswith(_PL_MARKERS)


def _undouble(t: str) -> str:
    return t[:-1] if len(t) >= 3 and t[-1] == t[-2] and t[-1] not in "lsfz" and t[-1] not in _VOWELS else t


def stem(token: str) -> str:
    """Light, deterministic suffix stripping; both sides of a comparison must use it.

    English: plural -s/-es/-ies, -ing, -ed, then a final -e ("times" and "time" both give "tim").
    Polish case endings come off words that look Polish (a Polish letter, or an ending such as -ami, -ych,
    -cji): "działów", "działy" and "działach" all give "dział". Words of 3 letters or fewer and tokens with
    digits are returned unchanged.
    """
    t = token.lower()
    if len(t) <= 3 or not t.isalpha():
        return t
    if _looks_polish(t):
        return next((t[: -len(e)] for e in _PL_ENDINGS if t.endswith(e) and len(t) - len(e) >= 3), t)
    if t.endswith("ies") and len(t) > 4:
        t = t[:-3] + "y"
    elif t.endswith("sses"):
        t = t[:-2]
    elif t.endswith("es") and len(t) > 4 and (t[-3] in "sxz" or t[-4:-2] in ("ch", "sh")):
        t = t[:-2]
    elif t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]
    if t.endswith("ing") and len(t) >= 6 and _VOWELS & set(t[:-3]):
        t = _undouble(t[:-3])
    elif t.endswith("ed") and not t.endswith("eed") and len(t) >= 5 and _VOWELS & set(t[:-2]):
        t = _undouble(t[:-2])
    return t[:-1] if t.endswith("e") and len(t) > 3 else t


# ---------------------------------------------------------------- numbers

_EN_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                        "fourteen fifteen sixteen seventeen eighteen nineteen".split())}  # fmt: skip
_EN_TENS = {w: 20 + 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_PL_FORMS = (  # value, then its forms
    "1 jeden jedna jedno jednego jednej jednemu jednym jedną|2 dwa dwie dwóch dwoma dwu dwom dwóm|3 trzy trzech trzem trzema"
    "|4 cztery czterech czterem czterema|5 pięć pięciu pięcioma|6 sześć sześciu sześcioma|7 siedem siedmiu siedmioma"
    "|8 osiem ośmiu ośmioma|9 dziewięć dziewięciu dziewięcioma|10 dziesięć dziesięciu dziesięcioma|11 jedenaście jedenastu"
    "|12 dwanaście dwunastu|13 trzynaście trzynastu|14 czternaście czternastu|15 piętnaście piętnastu|16 szesnaście szesnastu"
    "|17 siedemnaście siedemnastu|18 osiemnaście osiemnastu|19 dziewiętnaście dziewiętnastu|20 dwadzieścia dwudziestu"
    "|30 trzydzieści trzydziestu|40 czterdzieści czterdziestu|50 pięćdziesiąt pięćdziesięciu|100 sto stu"
)
NUMBER_WORDS: dict[str, int] = {**_EN_UNITS, **_EN_TENS, "dozen": 12, "hundred": 100}
NUMBER_WORDS.update({form: int(group.split()[0]) for group in _PL_FORMS.split("|") for form in group.split()[1:]})
_WORD_ALT = "|".join(sorted(map(re.escape, NUMBER_WORDS), key=len, reverse=True))
_NUMBER_WORD_RE = re.compile(
    rf"(?<!\w)(?:(?P<hund>(?:a|{'|'.join(_EN_UNITS)})\s+hundred)|(?P<dozen>a\s+dozen)"
    rf"|(?P<tens>{'|'.join(_EN_TENS)})(?:[\s-](?P<tunit>one|two|three|four|five|six|seven|eight|nine))?"
    rf"|(?P<word>{_WORD_ALT}))(?!\w)",
    re.IGNORECASE,
)
VAGUE_WORDS = ("dozens hundreds thousands many numerous multiple several dziesiątki dziesiątek dziesiątkami setki setek "
               "setkami kilkanaś* kilkunast* kilkadziesiąt* kilkudziesię* wiele wielu liczne licznych licznymi liczni").split()  # fmt: skip
_VAGUE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) for w in VAGUE_WORDS) + r")(?!\w)",
    re.IGNORECASE,
)
_DATE_RES = (
    re.compile(r"(?<![\w.])(?P<y>(?:19|20)\d{2})-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\w)"),
    re.compile(r"(?<![\w./])(?:0?[1-9]|[12]\d|3[01])[./](?:0?[1-9]|1[0-2])[./](?P<y>(?:19|20)\d{2})(?!\w)"),
    re.compile(r"(?<![\w./])(?:0?[1-9]|1[0-2])[./](?P<y>(?:19|20)\d{2})(?!\w)"),
)
_THOUSANDS = {",": r"[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?", ".": r"[1-9]\d{0,2}(?:\.\d{3})+(?:,\d+)?", " ": r"[1-9]\d{0,2}(?: \d{3})+(?:,\d+)?"}
_DIGITS_RE = re.compile(
    rf"(?<![\w.,])(?P<num>{'|'.join(_THOUSANDS.values())}|\d+(?:[.,]\d+)?)(?!\d)(?P<k>[kK](?!\w))?(?P<x>[x\u00d7](?!\w))?"
    r"(?P<plus>\+)?(?P<pct>\s?%|\s(?:percent|per\s+cent|procent\w*|proc\.))?(?![^\W_])",
    re.IGNORECASE,
)
_OVER_BEFORE = re.compile(r"(?:\bover|\bmore\s+than|\bponad)\s+$", re.IGNORECASE)
_PCT_AFTER = re.compile(r"\s?(?:%|percent\b|per\s+cent\b|procent\w*|proc\.)", re.IGNORECASE)


def _parse_digits(raw: str) -> float:
    for sep, rx in _THOUSANDS.items():
        if re.fullmatch(rx, raw):
            return float(raw.replace(sep, "").replace(",", "."))
    return float(raw.replace(",", "."))


def _polish_text(text: str) -> bool:
    return any(c in _PL_CHARS for c in text.lower())


def _free(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return all(end <= s or start >= e for s, e in spans)


def numbers(text: str) -> list[NumberMention]:
    """Every number mention in text order: digits (40,000 · 40 000 · 3,5 · 12% · 3+ · 40k · 2x), calendar years
    (also inside dates like 2024-02-12 or 03/2019), English and Polish number words (three, trzech) and vague
    quantities (dozens, kilkanaście: value -1.0, is_vague). has_plus is set for a trailing "+" and for
    "over", "more than" or "ponad" right before the number. Polish "ten" ("this") is not a number."""
    found: list[NumberMention] = []
    spans: list[tuple[int, int]] = []

    def add(m: re.Match, value: float, *, plus=False, year=False, pct=False, vague=False) -> None:
        spans.append((m.start(), m.end()))
        found.append(NumberMention(value=value, raw=m.group(0).strip(), start=m.start(), has_plus=plus,
                                   is_year=year, is_percent=pct, is_vague=vague))  # fmt: skip

    def over_before(start: int) -> bool:
        return bool(_OVER_BEFORE.search(text[max(0, start - 14) : start]))

    for rx in _DATE_RES:
        for m in rx.finditer(text):
            if _free(spans, m.start(), m.end()):
                add(m, float(m.group("y")), year=True)
    for m in _DIGITS_RE.finditer(text):
        if not _free(spans, m.start(), m.end()):
            continue
        num, suffix = m.group("num"), m.group("k") or m.group("x") or m.group("plus") or m.group("pct")
        add(m, _parse_digits(num) * (1000 if m.group("k") else 1), plus=bool(m.group("plus")) or over_before(m.start()),
            year=bool(re.fullmatch(r"(?:19|20)\d{2}", num)) and not suffix, pct=bool(m.group("pct")))  # fmt: skip
    polish = _polish_text(text)
    for m in _NUMBER_WORD_RE.finditer(text):
        if not _free(spans, m.start(), m.end()) or (polish and m.group(0).lower() == "ten"):
            continue
        if m.group("hund"):
            first = m.group("hund").split()[0].lower()
            value = 100.0 * (1 if first == "a" else _EN_UNITS[first])
        elif m.group("dozen"):
            value = 12.0
        elif m.group("tens"):
            value = float(_EN_TENS[m.group("tens").lower()] + (_EN_UNITS[m.group("tunit").lower()] if m.group("tunit") else 0))
        else:
            value = float(NUMBER_WORDS[m.group("word").lower()])
        add(m, value, plus=over_before(m.start()), pct=bool(_PCT_AFTER.match(text, m.end())))
    for m in _VAGUE_RE.finditer(text):
        if _free(spans, m.start(), m.end()):
            add(m, -1.0, vague=True)
    return sorted(found, key=lambda n: n.start)


# ---------------------------------------------------------------- durations

_YEAR = r"(?:19|20)\d{2}"
_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?"
    r"|nov(?:ember)?|dec(?:ember)?|sty(?:czeń|cznia)?|lut(?:y|ego)?|mar(?:zec|ca)?|kwi(?:ecień|etnia)?|maj(?:a)?"
    r"|cze(?:rwiec|rwca)?|lip(?:iec|ca)?|sie(?:rpień|rpnia)?|wrz(?:esień|eśnia)?|paź(?:dziernik|dziernika)?"
    r"|lis(?:topad|topada)?|gru(?:dzień|dnia)?"
)
_MONTH_NUM = {m: i % 12 + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec "
                                                  "sty lut mar kwi maj cze lip sie wrz paź lis gru".split())}  # fmt: skip
_MPFX = rf"(?:(?:0?[1-9]|1[0-2])[./]|(?:{_MONTH_NAMES})\.?\s+)?"
_PRESENT = r"(?:present|now|today|date|current|currently|ongoing|obecnie|teraz|nadal|dziś|dzisiaj|dzis|chwili\s+obecnej)"
_YEAR_WORD = r"(?:\s+roku|\s+r\.)?"
_RANGE_RE = re.compile(
    rf"(?<![\w/.])(?:(?:from|od)\s+)?(?P<m1>{_MPFX})(?P<y1>{_YEAR}){_YEAR_WORD}(?:\s*[-\u2013\u2014]\s*|\s+(?:to|until|till|do)\s+)"
    rf"(?:(?P<m2>{_MPFX})(?P<y2>{_YEAR}){_YEAR_WORD}|(?P<now>{_PRESENT}))(?!\w)",
    re.IGNORECASE,
)
_SINCE_RE = re.compile(rf"(?<![\w/.])(?:since|od)\s+(?:roku\s+)?(?P<m1>{_MPFX})(?P<y1>{_YEAR}){_YEAR_WORD}(?!\w)", re.IGNORECASE)
_UNIT_MONTHS = r"months?|miesięcy|miesiące|miesiąc|miesiąca"
_COUNT_RE = re.compile(
    rf"(?<![\w.,])(?P<over>(?:over|more\s+than|ponad|przeszło)\s+)?(?P<n1>\d+(?:[.,]\d+)?|{_WORD_ALT})"
    rf"(?:\s*[-\u2013]\s*(?P<n2>\d+(?:[.,]\d+)?|{_WORD_ALT}))?(?P<plus>\s?\+)?[\s-]+"
    rf"(?P<unit>years?|yrs?|lat|lata|latach|rok|roku|{_UNIT_MONTHS})(?!\w)",
    re.IGNORECASE,
)
_OVER_A_YEAR_RE = re.compile(r"(?<!\w)(?:over|more\s+than|ponad|przeszło)\s+(?:a\s+)?(?:year|rok)(?!\w)", re.IGNORECASE)


def _month(prefix: str | None) -> int | None:
    p = (prefix or "").strip().rstrip("./").strip().lower()
    return int(p) if p.isdigit() else _MONTH_NUM.get(p[:3])


def _count(raw: str) -> float:
    return float(NUMBER_WORDS[raw.lower()]) if raw.lower() in NUMBER_WORDS else float(raw.replace(",", "."))


def durations(text: str) -> list[DurationMention]:
    """Durations and year ranges in text order: "15 years", "3-5 years" (years = the upper bound), "over 12 years",
    "2010-2025", "03/2019 - present", "from 2021 to 2026", "od 2019", "od 2019 do obecnie", "ponad 10 lat",
    "18 months" (1.5 years). An open range ("od 2019", "2020-present") has end_year None and years None;
    the caller decides what "now" is."""
    found: list[DurationMention] = []
    spans: list[tuple[int, int]] = []

    def add(m: re.Match, years: float | None, start_year=None, end_year=None, over=False) -> None:
        spans.append((m.start(), m.end()))
        found.append(DurationMention(raw=m.group(0), start=m.start(), years=years, start_year=start_year,
                                     end_year=end_year, has_over=over))  # fmt: skip

    for m in _RANGE_RE.finditer(text):
        y1, y2 = int(m.group("y1")), int(m.group("y2")) if m.group("y2") else None
        if y2 is None:
            add(m, None, y1)
        elif y2 >= y1:
            m1, m2 = _month(m.group("m1")), _month(m.group("m2"))
            add(m, round(y2 - y1 + ((m2 - m1) / 12 if m1 and m2 else 0.0), 2), y1, y2)
    for m in _SINCE_RE.finditer(text):
        if _free(spans, m.start(), m.end()):
            add(m, None, int(m.group("y1")))
    for m in _COUNT_RE.finditer(text):
        if not _free(spans, m.start(), m.end()):
            continue
        if m.group("n1").lower() == "ten" and (_polish_text(text) or m.group("unit").lower().startswith(("lat", "rok", "mies"))):
            continue  # Polish "ten rok" means "this year"
        n1, n2 = _count(m.group("n1")), _count(m.group("n2")) if m.group("n2") else None
        if n1 >= 1900 or (n2 or 0) >= 1900:
            continue  # "w 2016 roku" is a calendar year
        value = (n2 if n2 is not None else n1) / (12 if re.fullmatch(_UNIT_MONTHS, m.group("unit"), re.IGNORECASE) else 1)
        add(m, round(value, 2), over=bool(m.group("over") or m.group("plus")))
    for m in _OVER_A_YEAR_RE.finditer(text):
        if _free(spans, m.start(), m.end()):
            add(m, 1.0, over=True)
    return sorted(found, key=lambda d: d.start)


# ---------------------------------------------------------------- sentences

_SKIP_LINE_RE = re.compile(r"^(?:#{1,6}(?:\s|$)|([-*_])(?:\s*\1){2,}$|\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?$)")
_BULLET_RE = re.compile(r"^(?:[-*+\u2022\u25e6\u25aa\u2023\u2013\u2014]|\d{1,3}[.)])\s+")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SENT_BREAK_RE = re.compile(r"[.!?]+[\"')\]]*\s+")
_OPENS_SENTENCE = re.compile(r"[\"'(\[]?[0-9A-ZĄĆĘŁŃÓŚŹŻÄÖÜÉ]")
_ABBREVIATIONS = frozenset("e.g i.e etc vs mr mrs ms dr prof inc ltd co corp jr sr st no nr np tj itp itd ds min max ok "
                           "ul m.in approx dept ca tel godz pt pn wg zob".split())  # fmt: skip
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _split_paragraph(starts: list[tuple[int, int]], text: str) -> list[tuple[int, str]]:
    """Split a paragraph into sentences; starts maps text offsets to source line numbers."""
    out, begin = [], 0

    def piece(end: int) -> None:
        part = text[begin:end]
        offset = begin + len(part) - len(part.lstrip())
        if part.strip():
            out.append((max(no for off, no in starts if off <= offset), part.strip()))

    for m in _SENT_BREAK_RE.finditer(text):
        before = text[begin : m.start()].split()
        last = before[-1].lstrip("([\"'").lower() if before else ""
        abbreviation = text[m.start()] == "." and (last.rstrip(".") in _ABBREVIATIONS or re.fullmatch(r"[^\W\d_]", last))
        if _OPENS_SENTENCE.match(text, m.end()) and not abbreviation:
            piece(m.end())
            begin = m.end()
    piece(len(text))
    return out


def sentences(text: str) -> list[tuple[int, str]]:
    """Units of a document as (line number, text): one per bullet or list item, one per sentence elsewhere.

    Line numbers are 1-based in the text as given. Headings, rules, table separators, blank lines and
    comment-only lines are skipped; an inline comment such as a citation marker <!-- c:ID --> stays on its unit.
    A line continues the previous unit only when that unit does not end in . ! ? : ; and the line starts in
    lower case (wrapped text); a "-" at a wrapped line end is joined. Markdown emphasis, code ticks and link
    targets are removed from the unit text.
    """
    units: list[tuple[int, str]] = []
    block: list[tuple[int, str]] = []
    is_bullet = in_comment = False

    def flush() -> None:
        nonlocal block
        joined, starts = "", []
        for no, part in block:
            if joined.endswith("-") and joined[-2:-1].isalpha() and part[:1].islower():
                joined = joined[:-1]
            elif joined:
                joined += " "
            starts.append((len(joined), no))
            joined += part
        pieces = [(block[0][0], joined)] if block and is_bullet else _split_paragraph(starts, joined) if block else []
        for no, piece in pieces:
            cleaned = " ".join(_LINK_RE.sub(r"\1", piece).replace("**", "").replace("__", "").replace("`", "").split())
            if cleaned:
                units.append((no, cleaned))
        block = []

    for no, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        line = raw.strip()
        if in_comment:
            if "-->" not in line:
                continue
            in_comment, line = False, line.split("-->", 1)[1].strip()
        line = line.lstrip(">").strip() if line.startswith(">") else line
        content = _COMMENT_RE.sub("", line).strip()
        if "<!--" in content:  # a comment opens here and closes on a later line
            in_comment = True
            line = content = content.split("<!--", 1)[0].strip()
        if not content or _SKIP_LINE_RE.match(line):
            flush()
            continue
        if line.startswith("|") and line.endswith("|"):
            line = " ; ".join(cell.strip() for cell in line.strip("|").split("|"))
        bullet = _BULLET_RE.match(line)
        if not bullet and block and not block[-1][1].endswith((".", "!", "?", ":", ";")) and line[:1].islower():
            block.append((no, line))
            continue
        flush()
        block, is_bullet = [(no, line[bullet.end() :] if bullet else line)], bool(bullet)
    flush()
    return units


# ---------------------------------------------------------------- snippet, safe_name, home_relative


def snippet(text: str, start: int, end: int, width: int = 120) -> str:
    """A one-line quote of text[start:end] with context, at most width characters, "..." where cut."""
    start, end = max(0, min(start, len(text))), max(0, min(end, len(text)))
    end = max(start, end)
    if end - start < width - 6:
        room = width - (end - start) - 6
        lo = max(0, start - room // 2)
        hi = min(len(text), end + room - (start - lo))
        lo = max(0, lo - (room - (start - lo) - (hi - end)))
        if lo > 0 and 0 <= text.find(" ", lo, start) - lo < 15:
            lo = text.find(" ", lo, start) + 1
        if hi < len(text) and 0 <= hi - text.rfind(" ", end, hi) < 15 and text.rfind(" ", end, hi) != -1:
            hi = text.rfind(" ", end, hi)
        piece = ("..." if lo > 0 else "") + " ".join(text[lo:hi].split()) + ("..." if hi < len(text) else "")
    else:
        piece = " ".join(text[start:end].split())
    return piece if len(piece) <= width else piece[: width - 3].rstrip() + "..."


_FOLD = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ß": "ss", "æ": "ae", "Æ": "AE",
                       "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "TH", "ð": "d", "Ð": "D", "ı": "i"})  # fmt: skip
_WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))})


def safe_name(text: str) -> str:
    """ASCII-folded [a-z0-9-] name, at most 60 characters, never a Windows reserved name, never empty.
    "Specjalista ds. kadr (k/m)" -> "specjalista-ds-kadr-k-m"."""
    folded = unicodedata.normalize("NFKD", text.translate(_FOLD)).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:60].rstrip("-") or "untitled"
    return name + "-1" if name in _WINDOWS_RESERVED else name


def home_relative(path: Path) -> str:
    """Absolute paths under the home folder print as ~/...; other paths print unchanged."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        return p.as_posix()
    for cand, base in ((p, Path.home()), (p.resolve(), Path.home().resolve())):
        try:
            rel = cand.relative_to(base)
        except ValueError:
            continue
        return "~" if rel == Path(".") else "~/" + rel.as_posix()
    return str(p)
