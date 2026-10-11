"""Facts that an offer text states in plain words, in English and Polish: pay, work mode, contract form, country and city.

Each fact is set only when the text states it clearly. When the text points two ways (two different pay
ranges, remote and on-site with no word for hybrid), the fact stays unknown.
"""

import re

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


def read_salary(text: str) -> Salary | None:
    """The one pay range the text states, or None when there is none or the text names two different ones."""
    found: set[tuple[float | None, float | None, str]] = set()
    units: set[str] = set()
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
    if len(found) != 1 or len(units) > 1:
        return None
    lo, hi, code = next(iter(found))
    return make_salary(lo, hi, code, next(iter(units), None))


# ---------------------------------------------------------------- work mode and contract

_MODES = (("hybrid", r"hybrid\w*|hybrydow\w*"),
          ("remote", r"remote(?!\s+(?:teams?|stakeholders?|colleagues|candidates?|employees|workers?|interviews?))|zdaln\w*|work from home|wfh"),
          ("onsite", r"on-?site|on site|stacjonarn\w*|w biurze|in[- ]the[- ]office|in-office|office-based"))  # fmt: skip
_CONTRACTS = (("b2b", r"b2b"),
              ("contract", r"umow[aęy][\s-]+(?:zlecen\w*|o\s+dzie[lł]\w*)|contractor|contract\s+(?:basis|role|position|job|work)|fixed[- ]term"),
              ("employment", r"uop|umow[aęy]\s+o\s+prac[ęe]|employment\s+contract|contract\s+of\s+employment|permanent"))  # fmt: skip
_NEGATION = re.compile(r"(?<!\w)(?:no|not|non|without|bez|brak|nie)[\s-]+(?:\w+\s+)?$", re.IGNORECASE)


def _stated(text: str, pattern: str) -> bool:
    """True when the pattern occurs as a word or phrase that is not negated ("no remote", "bez pracy zdalnej")."""
    return any(not _NEGATION.search(text[max(0, m.start() - 18): m.start()]) for m in re.finditer(rf"(?<!\w)(?:{pattern})(?!\w)", text, re.IGNORECASE))


def read_mode(text: str) -> str | None:
    """remote, hybrid or onsite when the text names one. "Hybrid" wins, because hybrid offers describe their
    remote and office days; remote together with on-site, and no hybrid, stays unknown."""
    modes = [mode for mode, pattern in _MODES if _stated(text, pattern)]
    return "hybrid" if "hybrid" in modes else modes[0] if len(modes) == 1 else None


def read_contract(text: str) -> list[str]:
    """B2B, contract (zlecenie, contractor) and employment (umowa o pracę, UoP, permanent) as named in the text."""
    return [form for form, pattern in _CONTRACTS if _stated(text, pattern)]


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
_PLACE_LABEL = re.compile(r"^\W*(?:location|lokalizacja|miejsce pracy|place of work|miejscowość|office|biuro)\b", re.IGNORECASE)
_HEADER_LINES = 12


def read_place(text: str) -> tuple[list[str], str | None]:
    """(country codes, location text) from the first lines of the text and from lines labelled Location, Lokalizacja
    and the like. A country is set when exactly one is named or implied by a city; the location text is the Polish
    city, else the country."""
    lines = [line for line in text.splitlines() if line.strip()]
    scope = " \n".join(dict.fromkeys(lines[:_HEADER_LINES] + [line for line in lines if _PLACE_LABEL.match(line)]))
    cities = [city for city, pattern in _CITIES if re.search(rf"(?<!\w)(?:{pattern})(?!\w)", scope, re.IGNORECASE)]
    named = {code for code, (_, pattern) in _COUNTRIES.items() if re.search(rf"(?<!\w)(?:{pattern})(?!\w)", scope, re.IGNORECASE)}
    countries = named | ({"PL"} if cities else set())
    if len(countries) != 1:
        return [], None
    code = next(iter(countries))
    return [code], ", ".join(cities[:3]) if cities else _COUNTRIES[code][0]


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
