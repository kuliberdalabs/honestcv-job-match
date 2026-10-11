"""Seen store with TTL, file-hash store, dedupe."""

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from honestcv.models import Offer
from honestcv.workspace import atomic_write

_MERGE_FIELDS = ("company", "location", "remote", "level", "salary", "posted_at", "valid_through", "apply_url",
                 "requisition_id", "countries", "contract", "skills", "skills_nice", "languages")  # fmt: skip


class SeenStore:
    """What earlier scans already saw. Entries expire after ttl_days. is_new() answers from the file as it
    was loaded, so marking offers during a run does not change the answer for the rest of that run."""

    def __init__(self, path: Path, today: dt.date, ttl_days: int, seen: dict[str, dict], files: dict[str, str]):
        self.path, self.today, self.ttl_days = Path(path), today, ttl_days
        self._seen, self._files = seen, files
        self._loaded, self._loaded_files = frozenset(seen), frozenset(files)

    @classmethod
    def load(cls, path: Path, today: dt.date, ttl_days: int = 45) -> "SeenStore":
        """A missing, unreadable or damaged state file gives an empty store: the only cost is the "new" flags."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        seen, files = data.get("seen"), data.get("files")
        seen = {k: v for k, v in (seen if isinstance(seen, dict) else {}).items() if isinstance(v, dict) and _alive(v.get("date"), today, ttl_days)}
        files = {k: v for k, v in (files if isinstance(files, dict) else {}).items() if _alive(v, today, ttl_days)}
        return cls(path, today, ttl_days, seen, files)

    def is_new(self, key: str) -> bool:
        return key not in self._loaded

    def mark(self, key: str, decision: Literal["surfaced", "filtered", "below_threshold"]) -> None:
        self._seen[key] = {"date": self.today.isoformat(), "decision": decision}

    def file_seen(self, sha256: str) -> bool:
        return sha256 in self._loaded_files

    def mark_file(self, sha256: str) -> None:
        self._files[sha256] = self.today.isoformat()

    def save(self) -> None:
        """Atomic write; entries older than ttl_days are dropped."""
        body = {
            "schema": 1,
            "seen": {k: v for k, v in sorted(self._seen.items()) if _alive(v["date"], self.today, self.ttl_days)},
            "files": {k: v for k, v in sorted(self._files.items()) if _alive(v, self.today, self.ttl_days)},
        }
        atomic_write(self.path, json.dumps(body, indent=1, ensure_ascii=False) + "\n")


def _alive(stamp: object, today: dt.date, ttl_days: int) -> bool:
    try:
        return (today - dt.date.fromisoformat(str(stamp))).days <= ttl_days
    except ValueError:
        return False


def _norm(text: str | None) -> str:
    return re.sub(r"\W+", " ", (text or "").casefold()).strip()


def dedupe_key(offer: Offer) -> str:
    """req:<requisition id>, else url:<host and path>, else a hash of company, title and text."""
    if offer.requisition_id and offer.requisition_id.strip():
        return "req:" + offer.requisition_id.strip().lower()
    if offer.url:
        parts = urlsplit(offer.url.strip())
        return f"url:{parts.netloc.lower()}{parts.path.rstrip('/')}" + (f"?{parts.query}" if parts.query else "")
    text = hashlib.sha1(_norm(offer.description).encode("utf-8")).hexdigest()[:12]
    return f"txt:{_norm(offer.company)}|{_norm(offer.title)}|{text}"


def offer_id(offer: Offer) -> str:
    return hashlib.sha1(dedupe_key(offer).encode("utf-8")).hexdigest()[:10]


def _merge(group: list[Offer]) -> Offer:
    best = max(group, key=lambda o: len(o.description))  # the fullest text wins; a tie keeps the first
    rest = [o for o in group if o is not best]
    update: dict = {name: next((getattr(o, name) for o in rest if getattr(o, name)), getattr(best, name))
                    for name in _MERGE_FIELDS if not getattr(best, name)}  # fmt: skip
    links = [u for o in group for u in (o.url, *o.other_urls)]
    update["other_urls"] = [u for u in dict.fromkeys(links) if u and u != best.url]
    update["thin"] = all(o.thin for o in group)
    return best.model_copy(update=update)


def dedupe(offers: list[Offer]) -> list[Offer]:
    """Merge offers with the same key; the other links join other_urls. Offers with the same company and
    title but different keys stay, and the later ones get possible_duplicate_of unless both carry a
    requisition id. Running it twice gives the same list."""
    groups: dict[str, list[Offer]] = {}
    for offer in offers:
        groups.setdefault(dedupe_key(offer), []).append(offer)
    first_by_name: dict[tuple[str, str], Offer] = {}
    out = []
    for group in groups.values():
        offer = _merge(group)
        name = (_norm(offer.company), _norm(offer.title))
        first = first_by_name.setdefault(name, offer) if name[0] else offer
        twin = first.id if first is not offer and not (first.requisition_id and offer.requisition_id) else None
        out.append(offer.model_copy(update={"possible_duplicate_of": twin}))
    return out
