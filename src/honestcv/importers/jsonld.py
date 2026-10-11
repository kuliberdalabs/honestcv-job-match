"""schema.org JobPosting from saved HTML."""

import html
import json
from html.parser import HTMLParser

from honestcv.importers.urls import country_code, finalize_offer, make_salary, map_contract, to_date
from honestcv.models import Offer
from honestcv.textutil import plain

_MAX_DEPTH = 50  # nesting of lists, objects and @graph below which nodes are ignored


class _Page(HTMLParser):
    """Collects the JSON-LD blocks, the first h1, the title and the canonical link of a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.h1, self.title, self.canonical = "", "", None
        self._open: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        values = dict(attrs)
        if tag == "script" and "ld+json" in (values.get("type") or "").lower():
            self._open, self._buffer = "ld", []
        elif tag in ("h1", "title") and not getattr(self, tag) and not self._open:
            self._open, self._buffer = tag, []
        elif tag == "link" and (values.get("rel") or "").lower() == "canonical":
            self.canonical = values.get("href")

    def handle_data(self, data: str) -> None:
        if self._open:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._open and tag == ("script" if self._open == "ld" else self._open):
            text = "".join(self._buffer)
            if self._open == "ld":
                self.blocks.append(text)
            else:
                setattr(self, self._open, " ".join(text.split()))
            self._open = None


def json_ld_blocks(html_text: str) -> list[str]:
    """The text of every closed <script type="application/ld+json"> block of a page, in one linear pass."""
    page = _Page()
    page.feed(html_text)
    page.close()
    return page.blocks


def job_postings(blocks: list[str]) -> list[dict]:
    """Every JobPosting object in the JSON-LD blocks, including those inside @graph; damaged blocks are skipped
    and nodes nested deeper than 50 levels are ignored."""
    found: list[dict] = []

    def walk(node: object, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
        elif isinstance(node, dict):
            kinds = node.get("@type")
            if "JobPosting" in (kinds if isinstance(kinds, list) else [kinds]):
                found.append(node)
            walk(node.get("@graph", []), depth + 1)

    for block in blocks:
        try:
            walk(json.loads(block, strict=False))
        except (ValueError, RecursionError):  # a block nested deeper than the parser allows
            continue
    return found


def _name(value: object) -> str:
    if isinstance(value, list):
        return _name(value[0]) if value else ""
    return str(value.get("name") or "") if isinstance(value, dict) else str(value or "")


def posting_to_offer(posting: dict, *, source: str, attribution: str, page_url: str | None = None) -> Offer | None:
    title = " ".join(_name(posting.get("title") or posting.get("name")).split())
    if not title:
        return None
    places = posting.get("jobLocation")
    addresses = [p.get("address", p) if isinstance(p, dict) else p for p in (places if isinstance(places, list) else [places])]
    parts, countries = [], []
    for address in addresses:
        fields = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")] if isinstance(address, dict) else [address]
        parts += [_name(f) for f in fields if _name(f)]
        country = country_code(_name(address.get("addressCountry"))) if isinstance(address, dict) else None
        countries += [country] if country else []
    pay = posting.get("baseSalary") if isinstance(posting.get("baseSalary"), dict) else {}
    value = pay.get("value")
    quantity = value if isinstance(value, dict) else pay  # the amounts sit in baseSalary.value or directly in baseSalary
    single = quantity.get("value") if isinstance(value, dict) else value
    salary = make_salary(quantity.get("minValue") or single, quantity.get("maxValue") or single,
                         pay.get("currency") or quantity.get("currency"), quantity.get("unitText"))  # fmt: skip
    description = str(posting.get("description") or "")
    description = plain(html.unescape(description) if "&lt;" in description and "<" not in description else description)
    skills = posting.get("skills")
    if skills:
        description += "\nSkills: " + (", ".join(map(_name, skills)) if isinstance(skills, list) else _name(skills))
    identifier = posting.get("identifier")
    offer = Offer(
        id="", source=source, attribution=attribution, title=title, company=_name(posting.get("hiringOrganization")) or None,
        url=_name(posting.get("url")) or page_url, location=", ".join(dict.fromkeys(parts)) or None,
        countries=list(dict.fromkeys(countries)),
        remote="remote" if str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE" else None,
        contract=map_contract(" ".join(map(_name, posting["employmentType"])) if isinstance(posting.get("employmentType"), list)
                              else posting.get("employmentType")),  # fmt: skip
        salary=salary, description=description.strip(), posted_at=to_date(posting.get("datePosted")),
        valid_through=to_date(posting.get("validThrough")),
        requisition_id=(_name(identifier.get("value") if isinstance(identifier, dict) else identifier) or None),
        thin=not description.strip(),
    )  # fmt: skip
    return finalize_offer(offer)


def offers_from_html(html_text: str, *, source: str, attribution: str) -> list[Offer]:
    """JobPosting JSON-LD first; a page without it becomes one offer made from its readable text."""
    page = _Page()
    page.feed(html_text)
    page.close()
    offers = [o for p in job_postings(page.blocks) if (o := posting_to_offer(p, source=source, attribution=attribution, page_url=page.canonical))]
    body = plain(html_text)
    if offers or not body:
        return offers
    title = page.h1 or page.title or body.splitlines()[0][:120]
    return [finalize_offer(Offer(id="", source=source, attribution=attribution, title=title, url=page.canonical,
                                 description=body, thin=len(body) < 40))]  # fmt: skip
