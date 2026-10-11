"""Link cleaning and the small normalizers shared by the source adapters and the importers."""

import datetime as dt
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

from honestcv.models import Offer, Salary
from honestcv.state import offer_id

_TRACKING = re.compile(r"(utm_.*|fbclid|gclid|mc_.*|trk|trackingid|refid)", re.IGNORECASE)
_COUNTRIES = (
    "AL Albania|AD Andorra|AR Argentina|AM Armenia|AU Australia|AT Austria|BE Belgium|BA Bosnia and Herzegovina|BR Brazil|"
    "BG Bulgaria|CA Canada|CL Chile|CN China|CO Colombia|HR Croatia|CY Cyprus|CZ Czechia|CZ Czech Republic|DK Denmark|"
    "EE Estonia|FI Finland|FR France|GE Georgia|DE Germany|GR Greece|HU Hungary|IS Iceland|IN India|IE Ireland|IL Israel|"
    "IT Italy|JP Japan|LV Latvia|LI Liechtenstein|LT Lithuania|LU Luxembourg|MT Malta|MX Mexico|MD Moldova|ME Montenegro|"
    "NL Netherlands|NZ New Zealand|MK North Macedonia|NO Norway|PL Poland|PL Polska|PT Portugal|RO Romania|RS Serbia|"
    "SG Singapore|SK Slovakia|SI Slovenia|ZA South Africa|KR South Korea|ES Spain|SE Sweden|CH Switzerland|TR Turkey|"
    "TR Turkiye|UA Ukraine|AE United Arab Emirates|GB United Kingdom|GB UK|US United States|US USA|US United States of America"
)
_COUNTRY_NAMES = {name.casefold(): code for code, name in (item.split(" ", 1) for item in _COUNTRIES.split("|"))}
_CONTRACTS = (("internship", r"intern|sta[zż]"), ("b2b", r"b2b"), ("freelance", r"freelanc"),
              ("contract", r"contract|temporary|fixed term|zlecen|dzie[lł]o"),
              ("employment", r"full ?time|part ?time|permanent|employ|etat|umow[aęy] o prac"))  # fmt: skip
_UNITS = (("hour", r"hour|godz"), ("day", r"day|daily|dzie[nń]"), ("month", r"month|mies|\bmc\b"), ("year", r"year|annual|rok|roczn"))


def clean_url(url: str) -> str:
    """Remove tracking parameters (utm_*, fbclid, gclid, mc_*, trk, trackingId, refId) and the fragment.

    Other query parameters stay, byte for byte, because some job URLs need them. Anything that is not an
    http(s) link comes back unchanged.
    """
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return url
    kept = [seg for seg in parts.query.split("&") if seg and not _TRACKING.fullmatch(seg.split("=", 1)[0])]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), ""))


def web_link(url: str | None) -> str | None:
    """The cleaned link, or None when it is empty or not an http(s) link (javascript: and the like are dropped)."""
    if not url or not url.strip():
        return None
    cleaned = clean_url(url)
    parts = urlsplit(cleaned)
    return cleaned if parts.scheme.lower() in ("http", "https") and parts.netloc else None


def finalize_offer(offer: Offer) -> Offer:
    """Tidy the text fields, clean the links and set the id (first 10 hex of sha1 of the dedupe key)."""
    url, apply_url = web_link(offer.url), web_link(offer.apply_url)
    others = [u for u in dict.fromkeys(web_link(x) for x in offer.other_urls) if u and u != url]
    tidy = offer.model_copy(update={
        "title": " ".join(offer.title.split()),
        "company": " ".join((offer.company or "").split()) or None,
        "url": url, "apply_url": apply_url, "other_urls": others,
        "countries": list(dict.fromkeys(c.upper() for c in offer.countries)),
    })  # fmt: skip
    return tidy.model_copy(update={"id": offer_id(tidy)})


def country_code(name: object) -> str | None:
    """ISO 3166 alpha-2 for a country name or code ("Poland", "PL", a flag emoji before the name); None if unknown."""
    text = re.sub(r"^[^A-Za-z]+", "", str(name or "")).strip()
    if len(text) == 2 and text.isalpha():
        return "GB" if text.upper() == "UK" else text.upper()
    folded = text.casefold()
    best = max((n for n in _COUNTRY_NAMES if re.match(re.escape(n) + r"\b", folded)), key=len, default=None)
    return _COUNTRY_NAMES[best] if best else None


def map_contract(text: object) -> list[str]:
    """Contract forms named in free text ("Full-Time", "CONTRACTOR", "B2B"), in a fixed order."""
    folded = re.sub(r"[_-]+", " ", str(text or "").casefold())
    return [form for form, rx in _CONTRACTS if re.search(rx, folded)]


def salary_unit(text: object) -> str | None:
    """hour, day, month or year for words like "hourly", "1 YEAR", "per-month-salary"; None when not stated."""
    folded = str(text or "").casefold()
    return next((unit for unit, rx in _UNITS if re.search(rx, folded)), None)


def make_salary(low: object, high: object, currency: object, unit: object) -> Salary | None:
    """A Salary from loose values; zero and blank amounts mean "not stated"."""

    def amount(value: object) -> float | None:
        try:
            number = float(str(value).replace(" ", "").replace(" ", "").replace(",", ".")) if value not in (None, "") else 0
        except ValueError:
            return None
        return number if number > 0 else None

    low, high = amount(low), amount(high)
    if low is None and high is None:
        return None
    code = str(currency or "").strip().upper()
    return Salary(min=low, max=high, currency=code if len(code) == 3 and code.isalpha() else None, unit=salary_unit(unit))


def to_date(value: object) -> dt.date | None:
    """A date from a unix time (seconds or milliseconds), an ISO string, DD.MM.YYYY or an RFC 822 date; else None."""
    text = str(value or "").strip()
    try:
        if text.isdigit():
            seconds = int(text) / (1000 if int(text) > 10**11 else 1)
            return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).date() if seconds else None
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", text):
            day, month, year = map(int, text.split("."))
            return dt.date(year, month, day)
        try:
            return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return parsedate_to_datetime(text).date() if text else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None
