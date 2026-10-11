"""CSV, JSON, txt and md offer files."""

import csv
import io
import json
import re
from pathlib import Path

from honestcv.errors import HonestError
from honestcv.importers.urls import country_code, finalize_offer, make_salary, map_contract, to_date
from honestcv.models import Offer
from honestcv.textutil import plain

KNOWN_KEYS = frozenset("""title company url location countries remote contract level skills skills_nice languages salary_min
    salary_max currency salary_unit description posted_at valid_through requisition_id""".split())  # fmt: skip
_CHOICES = {
    "remote": {"remote": "remote", "fully remote": "remote", "100% remote": "remote", "zdalnie": "remote", "zdalna": "remote",
               "hybrid": "hybrid", "hybrydowo": "hybrid", "hybrydowa": "hybrid", "onsite": "onsite", "on site": "onsite",
               "office": "onsite", "stacjonarnie": "onsite", "stacjonarna": "onsite"},
    "level": {"intern": "intern", "internship": "intern", "trainee": "intern", "staz": "intern", "stażysta": "intern",
              "junior": "junior", "entry": "junior", "entry level": "junior", "młodszy": "junior", "mid": "mid", "middle": "mid",
              "regular": "mid", "senior": "senior", "starszy": "senior", "lead": "lead", "principal": "lead", "staff": "lead",
              "head": "lead"},
}  # fmt: skip


def read_text(path: Path) -> str:
    """File text as UTF-8; a Windows export in the Central European code page is read too."""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def text_offer(text: str, *, title: str | None, company: str | None, url: str | None, source: str, attribution: str) -> Offer:
    """One offer from free text. The title is `title`, else the first heading or line."""
    body = plain(text)
    if not body:
        raise HonestError("There is no text to import.", hint="Copy the offer text first, then run the command again.")
    first = next(line for line in body.splitlines() if line.strip())
    guess = re.sub(r"^[#>*\-\s]+", "", first).strip(" *_#")[:120].strip()
    offer = Offer(id="", source=source, attribution=attribution, title=(title or "").strip() or guess or "Untitled offer",
                  company=company, url=url, description=body, thin=len(body) < 40)  # fmt: skip
    return finalize_offer(offer)


def read_offer_file(path: Path) -> tuple[list[Offer], list[str]]:
    """Offers from a .csv, .json, .txt or .md file, plus plain-English notes about what was skipped or ignored."""
    kind, name, notes = path.suffix.lower().lstrip("."), path.name, []
    if kind in ("txt", "md"):
        return [text_offer(read_text(path), title=None, company=None, url=None, source="import:text", attribution=f"your file {name}")], notes
    text = read_text(path)
    if kind == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HonestError(f"{name} is not valid JSON ({exc.msg}).", path=name, line=exc.lineno,
                              hint="Check the commas and quotes near that line, or use a CSV file.") from None  # fmt: skip
        if isinstance(data, dict):
            data = data["offers"] if "offers" in data else [data]
        if not isinstance(data, list):
            raise HonestError(f"{name} has no list of offers.", path=name, hint='Use a list of offers, or {"offers": [...]}.')
        records, sep = data, r"[;,]"
    else:
        head = text.lstrip("﻿\n ").split("\n", 1)[0]
        delimiter = max(",;\t", key=head.count)
        rows = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        records = [{(k or "").strip().lower(): v for k, v in row.items() if k} for row in rows]
        sep = ";"
    unknown = sorted({k for r in records if isinstance(r, dict) for k in r} - KNOWN_KEYS - {"_note"})
    if unknown:
        notes.append(f"{name}: ignored unknown columns: {', '.join(map(str, unknown))}")
    offers = []
    for number, record in enumerate(records, start=2 if kind == "csv" else 1):
        offer = offer_from_record(record, source=f"import:{kind}", attribution=f"your file {name}",
                                  where=f"{name} {'row' if kind == 'csv' else 'item'} {number}", notes=notes, sep=sep)  # fmt: skip
        if offer:
            offers.append(offer)
    return offers, notes


def _items(value: object, sep: str) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in re.split(sep, str(value or "")) if p.strip()]


def offer_from_record(record: object, *, source: str, attribution: str, where: str, notes: list[str], sep: str = r"[;,]") -> Offer | None:
    """One offer from a CSV row or a JSON object (the keys of docs/import.md); None, with a note, if unusable."""
    if not isinstance(record, dict):
        notes.append(f"{where}: skipped, it is not an offer object")
        return None

    def text(key: str) -> str:
        return str(record.get(key) if record.get(key) is not None else "").strip()

    def choice(key: str) -> str | None:
        value = re.sub(r"[-_]+", " ", text(key).casefold())
        if value and value not in _CHOICES[key]:
            notes.append(f"{where}: {key} '{text(key)[:30]}' is not recognized and was left empty")
        return _CHOICES[key].get(value)

    title, url, description = text("title"), text("url"), plain(text("description"))
    if not title or not (description or url):
        notes.append(f"{where}: skipped, an offer needs a title and a description or a url")
        return None
    contract = []
    for item in _items(record.get("contract"), sep):
        found = map_contract(item)
        contract += found
        if not found:
            notes.append(f"{where}: contract '{item[:30]}' is not recognized and was left out")
    offer = Offer(
        id="", source=source, attribution=attribution, title=title, company=text("company") or None, url=url or None,
        location=text("location") or None, countries=[c for c in map(country_code, _items(record.get("countries"), sep)) if c],
        remote=choice("remote"), contract=list(dict.fromkeys(contract)), level=choice("level"),
        skills=[s.lower() for s in _items(record.get("skills"), sep)], skills_nice=[s.lower() for s in _items(record.get("skills_nice"), sep)],
        languages=[s.lower() for s in _items(record.get("languages"), sep)],
        salary=make_salary(record.get("salary_min"), record.get("salary_max"), record.get("currency"), record.get("salary_unit")),
        description=description, posted_at=to_date(text("posted_at")), valid_through=to_date(text("valid_through")),
        requisition_id=text("requisition_id") or None, thin=not description,
    )  # fmt: skip
    return finalize_offer(offer)
