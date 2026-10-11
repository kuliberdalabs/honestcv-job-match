"""Facts that an offer text states in plain words, in English and Polish: pay, work mode, contract form, country and city.

Each fact is set only when the text states it clearly, and only from a clear context: a short metadata line
("Kraków, Polska · praca hybrydowa", "B2B | 150-180 zł/h", "Location: Berlin"), or a sentence that carries a pay word
or a work phrase ("praca w Krakowie", "fully remote"). A budget, a product price or a customer country in running text
is never read as a fact. When the text points two ways (two different pay ranges, remote and on-site with no word for
hybrid), the fact stays unknown.
"""

import re
from functools import lru_cache
from typing import NamedTuple

from honestcv.importers.urls import MAX_DESCRIPTION, make_salary
from honestcv.models import Offer, Salary

# ---------------------------------------------------------------- pay

_CURRENCY = r"(?:(?<!\w)(?:PLN|zł|złotych|zlotych|zl|EUR|euro|USD|GBP|CHF|SEK|NOK|DKK|CZK)(?!\w)|(?<![A-Za-z])[€£$])"
_CODES = {"zł": "PLN", "złotych": "PLN", "zlotych": "PLN", "zl": "PLN", "euro": "EUR", "€": "EUR", "$": "USD", "£": "GBP"}
_NUMBER = r"(?:\d{1,3}(?:[ ,.]\d{3})+(?!\d)|\d+(?:[.,]\d+)?)"
_SEPARATOR = r"(?:[-\u2013\u2014]|\bto\b|\bdo\b)"  # hyphen, en dash, em dash
# A pay figure or range next to a currency. A number never starts in the middle of a "12 000" style group, so the
# scan stays linear on long runs of digits.
_PAY = re.compile(
    rf"(?:(?P<pre>{_CURRENCY})[ \t]?)?"
    rf"(?<![\w.,])(?!(?<=\d[ ,.])\d{{3}}(?!\d))(?P<lo>{_NUMBER})(?:[ \t]?(?P<lok>k|tys(?:\.|(?!\w))|tyś(?:\.|(?!\w)))(?!\w))?"
    rf"(?:[ \t]*(?P<mid>{_CURRENCY}))?"
    rf"(?:[ \t]*{_SEPARATOR}[ \t]*(?:(?P<mid2>{_CURRENCY})[ \t]?)?"
    rf"(?P<hi>{_NUMBER})(?:[ \t]?(?P<hik>k|tys(?:\.|(?!\w))|tyś(?:\.|(?!\w)))(?!\w))?)?"
    rf"(?:[ \t]*(?P<post>{_CURRENCY}))?",
    re.IGNORECASE,
)
_UNIT_AFTER = (("hour", r"h|hr|hours?|godz\w*"), ("day", r"days?|dzie[nń]"), ("month", r"months?|mies\w*|miesi[aą]c\w*|mc"),
               ("year", r"years?|yr|annum|rok|roku"))  # fmt: skip
_UNIT_WORD = (("hour", r"hourly|godzinowo"), ("day", r"daily|dziennie"), ("month", r"monthly|miesi[eę]cznie"),
              ("year", r"yearly|annually|rocznie"))  # fmt: skip
_BEFORE_MIN = re.compile(r"(?<!\w)(?:od|from|min\.?|minimum|at least|starting at|ponad|powyżej)\s*$", re.IGNORECASE)
_BEFORE_MAX = re.compile(r"(?<!\w)(?:do|up to|max\.?|maximum|poniżej)\s*$", re.IGNORECASE)
_SALARY_WORD = re.compile(r"wynagrodz|zarob|stawk|widełk|pensj|salary|(?<!\w)pay(?!\w)|compensation|(?<!\w)rate(?!\w)|(?<!\w)earn|wage|oferujemy|we offer", re.IGNORECASE)
_GROSS_NET = re.compile(r"\s*(?:brutto|netto|gross|net)(?!\w)", re.IGNORECASE)
_MAX_PAY = 1_000_000
# A sentence of running text is read for pay only when it carries a pay word and no word for a budget, a price or a customer.
_PAY_WORD = re.compile(r"(?<!\w)(?:salar(?:y|ies)|pay|oferujemy|we\s+offer|zarobk\w*|zarabia\w*|earn(?:s|ing)?|miesi[eę]cznie|compensation|wages?|rates?|wynagrodz\w*|pensj\w*|stawk\w*|wide[łl]k\w*|brutto|netto|gross|net)(?!\w)", re.IGNORECASE)
_NOT_PAY = re.compile(r"(?<!\w)(?:budget\w*|bud[zż]et\w*|costs?|koszt\w*|pric(?:e|es|ing)|cen[aęyą]|cenie|cenach|revenues?|przych[oó]d\w*|turnover|obr[oó]t\w*"
                      r"|clients?|klient\w*|funding|training\w*|szkole\w*|bonus\s+pool)(?!\w)", re.IGNORECASE)  # fmt: skip
_SENTENCES = re.compile(r"(?<=[.!?;])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9€$£])")
_PAY_TAIL = re.compile(rf"(?:/|(?<!\w)(?:per|a|an|na|za)(?!\w))[ \t]*(?:{'|'.join(w for _, w in _UNIT_AFTER)})(?!\w)"
                       rf"|(?<!\w)(?:{'|'.join(w for _, w in _UNIT_WORD)})(?!\w)", re.IGNORECASE)  # fmt: skip


def _amount(raw: str, thousands: bool) -> float:
    grouped = re.fullmatch(r"\d{1,3}(?:[ ,.]\d{3})+", raw)
    value = float(re.sub(r"[ ,.]", "", raw)) if grouped else float(raw.replace(",", "."))
    return value * 1000 if thousands else value


def _unit(tail: str) -> str | None:
    """The pay period named right after a figure: "/mies.", "per month", "a year", "/h", "rocznie"."""
    tail = re.split(r"[.;:]\s|\n", tail[:30])[0]
    found = []
    for unit, words in _UNIT_AFTER:
        if m := re.search(rf"(?:/|(?<!\w)(?:per|a|an|na|za)(?!\w))[ \t]*(?:{words})(?!\w)", tail, re.IGNORECASE):
            found.append((m.start(), unit))
    for unit, words in _UNIT_WORD:
        if m := re.search(rf"(?<!\w)(?:{words})(?!\w)", tail, re.IGNORECASE):
            found.append((m.start(), unit))
    return min(found)[1] if found else None


def _collect_pay(text: str, found: set[tuple[float | None, float | None, str]], units: set[str]) -> None:
    """Add every pay range in `text` to `found`, and the pay periods named next to them to `units`."""
    for m in _PAY.finditer(text):
        codes = {_CODES.get(c.casefold(), c.upper()) for c in (m["pre"], m["mid"], m["mid2"], m["post"]) if c}
        if len(codes) != 1:
            continue
        lo = _amount(m["lo"], bool(m["lok"]))
        hi = _amount(m["hi"], bool(m["hik"])) if m["hi"] else None
        if hi is not None and m["hik"] and not m["lok"] and lo < 1000:
            lo *= 1000  # "12-18k"
        before = text[max(0, m.start() - 80): m.start()].rsplit("\n", 1)[-1]
        tail = text[m.end(): m.end() + 40]
        unit = _unit(tail)
        if hi is None:
            if _BEFORE_MIN.search(before):
                hi = None
            elif _BEFORE_MAX.search(before) and (unit or _GROSS_NET.match(tail) or _SALARY_WORD.search(before)):
                lo, hi = None, lo
            elif unit or _GROSS_NET.match(tail) or _SALARY_WORD.search(before):
                hi = lo
            else:
                continue
        elif lo > hi:
            continue
        if max(lo or 0, hi or 0) > _MAX_PAY:
            continue
        found.add((lo, hi, codes.pop()))
        units.update([unit] if unit else [])


def read_salary(text: str) -> Salary | None:
    """The one pay range the text states, or None when there is none or the text names two different ones. A range is
    read from a metadata line, or from a sentence with a pay word; never from a budget, a price or a customer figure."""
    found: set[tuple[float | None, float | None, str]] = set()
    units: set[str] = set()
    for line in _unique_lines(text):
        if _meta(line).meta:
            if not _NOT_PAY.search(line):
                _collect_pay(line, found, units)
            continue
        for sentence in _SENTENCES.split(line):
            if _PAY_WORD.search(sentence) and not _NOT_PAY.search(sentence):
                _collect_pay(sentence, found, units)
    if len(found) != 1 or len(units) > 1:
        return None
    lo, hi, code = next(iter(found))
    return make_salary(lo, hi, code, next(iter(units), None))


# ---------------------------------------------------------------- work mode and contract

# What a metadata line says with one word ("Hybrid", "Remote", "B2B"), and the phrases that say it in running text.
_NOT_A_MODE = r"teams?|stakeholders?|colleagues|candidates?|employees|workers?|interviews?|sensing|sensors?|control|monitoring|access|desktop|devices?|servers?|support|customers?|clients?|users?"
_DAYS = r"(?:[1-4]|one|two|three|four|jeden|dwa|dwie|trzy|cztery)"
_DAYS_IN_OFFICE = (rf"{_DAYS}\s+(?:days?|dni|dzie[nń])\s+(?:(?:a|per|each|every)\s+week\s+|w\s+tygodniu\s+|tygodniowo\s+)?(?:in|at|w)\s+(?:the\s+|our\s+)?(?:office|biurze)"
                   rf"|in\s+the\s+office\s+{_DAYS}\s+days?|w\s+biurze\s+{_DAYS}\s+(?:dni|dzie[nń])")
_ALONE = r"(?=\s*(?:$|[.!?)\]/|(]|\s(?:or|and|lub|albo|i|oraz)\b))"  # the word stands alone, not in front of a noun
_MODES = (("hybrid", rf"hybrid\w*|hybrydow\w*|{_DAYS_IN_OFFICE}"),
          ("remote", rf"remote(?!\s+(?:{_NOT_A_MODE}))|zdaln\w*|work from home|wfh"),
          ("onsite", r"on-?site|on site|stacjonarn\w*|w biurze|in[- ]the[- ]office|in-office|office-based"))  # fmt: skip
_MODE_PHRASES = (
    ("hybrid", rf"hybrid\s+(?:work|working|model|setup|set-up|role|position|arrangement)|(?:prac\w*|tryb\w*|model\w*)\s+hybrydow\w*|hybrydow\w*\s+(?:prac\w*|tryb\w*|model\w*)|hybrydowo|work(?:ing)?\s+hybrid|{_DAYS_IN_OFFICE}"),
    ("remote", rf"fully[- ]remote|(?:100\s*%|w\s+pełni)\s*(?:remote|zdaln\w*)|remote[- ](?:first|work|working|position|role|job|only|opportunity|setup|set-up|model)|work(?:s|ing)?\s+remotely"
               rf"|prac\w*\s+(?:w\s+trybie\s+)?zdaln\w*|zdaln\w*\s+prac\w*|tryb\s+zdaln\w*|work from home|wfh|remote{_ALONE}"),
    ("onsite", r"on-?site(?!\s+(?:teams?|visits?|support|installations?|training|clients?|customers?|meetings?|events?|interviews?|audits?|services?|inspections?|assessments?|engineers?|technicians?|testing|presence)(?!\w))"
               r"|stacjonarnie|prac\w*\s+stacjonarn\w*|stacjonarn\w*\s+prac\w*|w\s+biurze"),
)  # fmt: skip
_CONTRACTS = (("b2b", r"b2b"),
              ("contract", r"umow[aęy][\s-]+(?:zlecen\w*|o\s+dzie[lł]\w*)|contractor|contract\s+(?:basis|role|position|job|work)|fixed[- ]term"),
              ("employment", r"uop|umow[aęy]\s+o\s+prac[ęe]|employment\s+contract|contract\s+of\s+employment|permanent"))  # fmt: skip
_OR = r"(?:\s+(?:lub|albo|or)\s+|\s*/\s*)"
_CONTRACT_PHRASES = (
    ("b2b", rf"b2b\s+(?:contract|basis|cooperation|model|agreement|engagement|kontrakt\w*)|(?:kontrakt\w*|umow\w*|wsp[oó]łprac\w*|formie|modelu)\s+b2b"
            rf"|(?:uop|umow\w*\s+o\s+prac\w*){_OR}b2b|b2b{_OR}(?:uop|umow\w*\s+o\s+prac\w*)"),  # "umowa o pracę lub B2B"
    ("contract", r"umow[aęy][\s-]+(?:zlecen\w*|o\s+dzie[lł]\w*)|contractor\s+(?:role|position|basis|agreement)|contract\s+(?:basis|role|position|job|work)|fixed[- ]term\s+(?:contract|employment|position|role)"),
    ("employment", r"uop|umow[aęy]\s+o\s+prac[ęe]|employment\s+contract|contract\s+of\s+employment|permanent\s+(?:contract|employment|position|role|job|basis)"),
)  # fmt: skip
_NEGATION = re.compile(r"(?<!\w)(?:no|not|non|without|bez|brak|nie)[\s-]+(?:\w+\s+)?$", re.IGNORECASE)


def _compile(patterns: tuple[tuple[str, str], ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((name, re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE)) for name, pattern in patterns)


_MODE_WORDS, _MODE_SENTENCES = _compile(_MODES), _compile(_MODE_PHRASES)
_CONTRACT_WORDS, _CONTRACT_SENTENCES = _compile(_CONTRACTS), _compile(_CONTRACT_PHRASES)


def _stated(text: str, pattern: re.Pattern[str]) -> bool:
    """True when the pattern occurs as a word or phrase that is not negated ("no remote", "bez pracy zdalnej")."""
    return any(not _NEGATION.search(text[max(0, m.start() - 18): m.start()]) for m in pattern.finditer(text))


def _names_in(lines: list[str], words: tuple, phrases: tuple) -> set[str]:
    """The facts of one kind that the lines state: a metadata line by its words, any other line by its phrases only."""
    found: set[str] = set()
    for line in lines:
        for name, pattern in words if _meta(line).meta else phrases:
            if name not in found and _stated(line, pattern):
                found.add(name)
    return found


def read_mode(text: str) -> str | None:
    """remote, hybrid or onsite when the text names one. "Hybrid" wins, because hybrid offers describe their
    remote and office days; remote together with on-site, and no hybrid, stays unknown. A metadata line says it in
    a word ("Remote", "Hybrydowo"); other text needs a phrase ("fully remote", "praca zdalna", "on-site"): never
    "remote sensing", "remote teams" or "remote access"."""
    modes = _names_in(_unique_lines(text), _MODE_WORDS, _MODE_SENTENCES)
    return "hybrid" if "hybrid" in modes else modes.pop() if len(modes) == 1 else None


def read_contract(text: str) -> list[str]:
    """B2B, contract (zlecenie, contractor) and employment (umowa o pracę, UoP, permanent) as named in the text: on a
    metadata line by the word, elsewhere by a phrase ("umowa o pracę", "B2B contract", "kontrakt B2B")."""
    forms = _names_in(_unique_lines(text), _CONTRACT_WORDS, _CONTRACT_SENTENCES)
    return [form for form, _ in _CONTRACTS if form in forms]


# ---------------------------------------------------------------- country and city

_COUNTRIES = {
    "PL": ("Poland", r"polska|polsce|polskę|poland"), "SE": ("Sweden", r"sverige|sweden|szwecja|szwecji|szwecję"),
    "DE": ("Germany", r"deutschland|germany|niemcy|niemczech"), "CZ": ("Czechia", r"czechia|czech republic|czechy|czechach"),
    "SK": ("Slovakia", r"slovakia|slovensko|słowacja|słowacji"), "NL": ("Netherlands", r"netherlands|nederland|holandia|holandii|niderlandy"),
    "FR": ("France", r"france|francja|francji"), "ES": ("Spain", r"spain|españa|hiszpania|hiszpanii"),
    "IT": ("Italy", r"italy|italia|włochy|włoszech"), "GB": ("United Kingdom", r"united kingdom|great britain|wielka brytania|wielkiej brytanii|uk"),
    "IE": ("Ireland", r"ireland|irlandia|irlandii"), "US": ("United States", r"united states|usa|stany zjednoczone|stanach zjednoczonych"),
    "NO": ("Norway", r"norway|norge|norwegia|norwegii"), "DK": ("Denmark", r"denmark|danmark"),
    "FI": ("Finland", r"finland|suomi|finlandia|finlandii"), "AT": ("Austria", r"austria|österreich|austrii"),
    "CH": ("Switzerland", r"switzerland|schweiz|szwajcaria|szwajcarii"), "LT": ("Lithuania", r"lithuania|litwa|litwie"),
    "LV": ("Latvia", r"latvia|łotwa|łotwie"), "EE": ("Estonia", r"estonia|estonii"), "PT": ("Portugal", r"portugal|portugalia|portugalii"),
    "BE": ("Belgium", r"belgium|belgia|belgii"), "UA": ("Ukraine", r"ukraine|ukraina|ukrainie"),
}  # fmt: skip
# The 20 largest Polish cities, with the endings they take in a sentence ("w Krakowie", "we Wrocławiu").
_CITIES = (
    ("Warszawa", r"warszaw\w*|warsaw"), ("Kraków", r"krak[oó]w\w*|cracow"), ("Łódź", r"łódź|łodzi|lodz"), ("Wrocław", r"wroc[łl]aw\w*"),
    ("Poznań", r"pozna[ńn]|poznaniu"), ("Gdańsk", r"gda[ńn]sk\w*"), ("Szczecin", r"szczecin\w*"), ("Bydgoszcz", r"bydgoszcz\w*"),
    ("Lublin", r"lublin\w*"), ("Białystok", r"bia[łl]ystok\w*|bia[łl]ymstoku"), ("Katowice", r"katowic\w*"), ("Gdynia", r"gdyni\w*"),
    ("Częstochowa", r"cz[ęe]stochow\w*"), ("Radom", r"radom(?:iu|ia)?"), ("Rzeszów", r"rzesz[óo]w|rzeszowie|rzeszowa"),
    ("Toruń", r"toru[ńn]|toruniu|torunia"), ("Sosnowiec", r"sosnowiec|sosnowcu|sosnowca"), ("Kielce", r"kielc\w*"),
    ("Gliwice", r"gliwic\w*"), ("Olsztyn", r"olsztyn\w*"),
)  # fmt: skip
_REGIONS = r"eu|ue|europe|europa|emea|eea|cee|nordics|apac|latam|worldwide|anywhere"
_WORK_PLACE = re.compile(
    r"(?<!\w)(?:(?:based|located|headquartered|situated)\s+in|offices?\s+(?:(?:is|are)\s+)?(?:located\s+)?in|headquarters\s+(?:(?:is|are)\s+)?in|work\s+from\s+our"
    r"|prac(?:a|y|ę|e|ujesz|ujemy)\s+w(?:e)?|biur[oa]\s+w(?:e)?|siedzib[aąy]\s+w(?:e)?|lokalizacja)\s+(?P<tail>[^.;:!?\n]{1,50})",
    re.IGNORECASE,
)  # fmt: skip
_TAIL_CUT = re.compile(r",\s+(?!the\s)(?=[a-ząćęłńóśźż])|\s(?:dla|for|with|where|which|that|but|while|ale|gdzie|aby|żeby)\s")
_HEADER_LINES = 12


def _work_places(line: str) -> list[str]:
    """What follows a work-location phrase ("based in", "office in", "praca w", "biuro w", "lokalizacja") in the line."""
    return [_TAIL_CUT.split(m["tail"])[0] for m in _WORK_PLACE.finditer(line)]


def read_place(text: str) -> tuple[list[str], str | None]:
    """(country codes, location text) from the metadata lines among the first lines of the text, from lines labelled
    Location, Lokalizacja and the like, and from work-location phrases in the first lines ("praca w Krakowie", "based in
    Berlin"). A mention in running text ("we sell in Sweden", "our customers are in Poland") is not read. A country is
    set when exactly one is named or implied by a city; the location text is the Polish city, else the country."""
    lines = [line for line in text.splitlines() if line.strip()]
    pieces: list[str] = []
    for index, line in enumerate(lines):
        if index >= _HEADER_LINES:
            if (label := _label(line)) and label.place:
                pieces.append(label.value)
        elif (found := _meta(line)).meta:
            pieces.append(found.value)
        else:
            pieces.extend(_work_places(line))
    scope = " \n".join(dict.fromkeys(pieces))
    cities = [city for city, pattern in _CITIES if re.search(rf"(?<!\w)(?:{pattern})(?!\w)", scope, re.IGNORECASE)]
    named = {code for code, (_, pattern) in _COUNTRIES.items() if re.search(rf"(?<!\w)(?:{pattern})(?!\w)", scope, re.IGNORECASE)}
    countries = named | ({"PL"} if cities else set())
    if len(countries) != 1:
        return [], None
    code = next(iter(countries))
    return [code], ", ".join(cities[:3]) if cities else _COUNTRIES[code][0]


# ---------------------------------------------------------------- metadata lines

# A metadata line is short and is either a label line ("Lokalizacja: Kraków", "Salary: ...") or says nothing but
# places, work modes, contract forms, pay and the small words between them ("Kraków, Polska · praca hybrydowa").
_META_MAX = 80
_LABEL = re.compile(r"^[\W_]*(?P<label>[^\W\d_][\w .\-/()]{0,40}?)[\s*_]*:[\s*_]*(?P<value>.*)$")
_PLACE_KEYS = re.compile(r"(?<!\w)(?:location|lokalizacja|miejsce|miejscowość|place of work|city|miasto|country|kraj|office|biuro|siedziba)(?!\w)", re.IGNORECASE)
_LABEL_KEYS = re.compile(
    r"(?<!\w)(?:rate|salary|pay|compensation|wage|wynagrodzenie|wide[łl]ki|stawka|pensja|zarobki|contract|umowa|umowy|employment|zatrudnienie|tryb|model pracy|system pracy"
    r"|rodzaj pracy|typ pracy|work (?:mode|model|type|arrangement|setup)|working (?:mode|model)|forma wsp[oó]łpracy|rodzaj wsp[oó]łpracy)(?!\w)",
    re.IGNORECASE,
)  # fmt: skip
_FILLER = frozenset("""a an and or oraz lub albo i w we z ze na za do od of in at the to up from per ca approx około min max minimum maximum plus
    work working job role position praca pracy pracę stanowisko salary pay compensation rate wage wynagrodzenie stawka widełki pensja zarobki
    brutto netto gross net vat location lokalizacja miejsce miejscowość office biuro city miasto country kraj siedziba contract umowa umowy
    kontrakt współpraca wspolpraca employment zatrudnienie type typ rodzaj forma mode tryb model system day days dni dzień week weeks tydzień
    tygodniu tygodniowo month months mies miesiąc hour hours godz year rok""".split())  # fmt: skip
_SEGMENTS = re.compile(r"[,;|·•()\[\]:/\\]|[.!?](?=\s|$)|\s[-\u2013\u2014]+\s")
_WORDS = re.compile(r"\x00|[^\W_]+")
_PLACE_NAMES = re.compile(
    rf"(?<!\w)(?:{'|'.join(p for _, p in _COUNTRIES.values())}|{'|'.join(p for _, p in _CITIES)}|{_REGIONS})(?!\w)", re.IGNORECASE
)
_FACT_NAMES = re.compile(
    rf"(?<!\w)(?:{'|'.join(p for _, p in _MODES + _MODE_PHRASES + _CONTRACTS + _CONTRACT_PHRASES)})(?!\w)", re.IGNORECASE
)


class _Label(NamedTuple):
    value: str  # what follows the colon
    place: bool  # the label asks for a place: Location, Lokalizacja, Miejsce pracy


class _Line(NamedTuple):
    meta: bool
    value: str  # the text after the label of a label line, else the whole line


@lru_cache(maxsize=8192)
def _label(line: str) -> _Label | None:
    """The label and value of a short "Label: value" line whose label names a place, pay, work mode or contract."""
    text = line.strip()
    if len(text) > _META_MAX or not (m := _LABEL.match(text)) or len(m["label"].split()) > 3:
        return None
    place = bool(_PLACE_KEYS.search(m["label"]))
    return _Label(m["value"], place) if place or _LABEL_KEYS.search(m["label"]) else None


def _mark(text: str) -> tuple[str, int]:
    """The text with each pay figure, work mode, contract form and place replaced by a NUL, and the count of places."""
    text = _PAY.sub(lambda m: " \x00 " if any(m[g] for g in ("pre", "mid", "mid2", "post")) else m[0], text)
    text = _FACT_NAMES.sub(" \x00 ", _PAY_TAIL.sub(" ", text))
    return _PLACE_NAMES.subn(" \x00 ", text)


def _says_only_facts(text: str) -> bool:
    """True when every part of the line (parts end at a comma, a bar, a dot, a colon...) is a fact, small words and
    numbers around facts, or, in a line that names a place, a capitalised place name of up to three words."""
    marked, places = _mark(text)
    facts = names = 0
    for part in _SEGMENTS.split(marked):
        words = _WORDS.findall(part)
        rest = [w for w in words if w != "\x00"]
        facts += len(words) - len(rest)
        if all(w.casefold() in _FILLER or w.isdigit() for w in rest):
            continue
        if len(rest) == len(words) and places and len(rest) <= 3 and all(w[0].isupper() for w in rest):
            names += 1
        else:
            return False
    return facts > 0 and names <= 3


@lru_cache(maxsize=8192)
def _meta(line: str) -> _Line:
    """Whether the line is a metadata line, and the text of it that holds the facts."""
    text = line.strip()
    if len(text) > _META_MAX or "\x00" in text:
        return _Line(False, text)
    if label := _label(text):
        return _Line(True, label.value)
    return _Line(_says_only_facts(text), text)


def _unique_lines(text: str) -> list[str]:
    return list(dict.fromkeys(line for line in text.splitlines() if line.strip()))


# ---------------------------------------------------------------- the offer

def with_text_facts(offer: Offer) -> Offer:
    """The offer with pay, work mode, contract form, country and location read from its description, for the fields
    that are still empty. A fact the offer already carries is never replaced."""
    text = offer.description[:MAX_DESCRIPTION]
    if not text:
        return offer
    update: dict = {}
    if offer.salary is None and (salary := read_salary(text)):
        update["salary"] = salary
    if offer.remote is None and (mode := read_mode(text)):
        update["remote"] = mode
    if not offer.contract and (contract := read_contract(text)):
        update["contract"] = contract
    if not offer.countries and not offer.location:
        countries, location = read_place(text)
        if countries:
            update.update(countries=countries, location=location)
    return offer.model_copy(update=update) if update else offer
