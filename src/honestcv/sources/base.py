"""Adapter protocol, drift canary."""

import datetime as dt
import html
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from honestcv.config import SearchConfig
from honestcv.errors import FetchNotAllowed, HonestError, SourceDrift, SourceRefused
from honestcv.importers.urls import finalize_offer
from honestcv.models import Offer, SourceStatus
from honestcv.sources.http import HttpClient, OfflineSkip, RobotsUnreadable
from honestcv.textutil import plain

__all__ = ["Adapter", "SourceDrift", "expect", "make_offer", "map_entries", "run_adapter"]


class Adapter(Protocol):
    id: str

    def fetch(self, http: HttpClient, search: SearchConfig, today: dt.date) -> list[Offer]: ...


def expect(value: object, kind: type, what: str) -> Any:
    """value if it has the expected type, else SourceDrift: the answer is not shaped the way the adapter reads it."""
    if not isinstance(value, kind):
        raise SourceDrift(f"{what} is missing or has a new shape.")
    return value


def make_offer(source: str, attribution: str, title: object, url: object, *, html_text: object = "", **fields: Any) -> Offer | None:
    """An Offer from an API entry; None when it has no title or no http(s) link. `html_text` is turned into plain text."""
    title, url = " ".join(html.unescape(str(title or "")).split()), str(url or "").strip()
    if not title or not url:
        return None
    if isinstance(fields.get("company"), str):
        fields["company"] = html.unescape(fields["company"])
    text = plain(str(html_text or ""))
    offer = finalize_offer(Offer(id="", source=source, attribution=attribution, title=title, url=url, description=text, thin=not text, **fields))
    return offer if offer.url else None


def map_entries(entries: Iterable[Any], mapper: Callable[[Any], Offer | None]) -> list[Offer]:
    """Offers from raw entries. An entry the mapper cannot read is dropped; when more than half of the entries
    are unusable (no title or no link) the source's shape has changed, and that is SourceDrift."""
    items = list(entries)
    offers = []
    for entry in items:
        try:
            offer = mapper(entry)
        except (KeyError, TypeError, AttributeError, ValueError, IndexError):
            offer = None
        if offer is not None:
            offers.append(offer)
    if items and len(offers) * 2 < len(items):
        raise SourceDrift("More than half of the entries have no title or link.")
    return offers


def _plural(n: int, word: str = "offer") -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def run_adapter(adapter: Adapter, http: HttpClient, search: SearchConfig, today: dt.date) -> tuple[list[Offer], SourceStatus]:
    """Run one adapter and describe the outcome. A failure never raises: it becomes a state the report can show.

    Drift canary: an adapter raises SourceDrift when the answer lacks the list it reads, and map_entries raises it
    when most entries are unusable. A readable answer with zero offers is `empty`, never "no matches" by silence.
    """
    sid = adapter.id

    def status(state: str, message: str, offers: int = 0, code: int | None = None) -> SourceStatus:
        return SourceStatus(id=sid, state=state, http_status=code or http.last_status.get(sid), offers=offers, message=message)  # type: ignore[arg-type]

    lost = "Results from it are missing"
    try:
        offers = adapter.fetch(http, search, today)
    except OfflineSkip:
        return [], status("skipped", "offline, and nothing is cached")
    except RobotsUnreadable as exc:
        return [], status("skipped", exc.message)
    except SourceRefused as exc:
        code = getattr(exc, "http_status", None)
        return [], status("refused", f"{sid} refused access (HTTP {code}); results from it are missing" if code
                          else f"{sid}: robots.txt disallows this path; results from it are missing", code=code)  # fmt: skip
    except (SourceDrift, ValueError, KeyError, TypeError, AttributeError, IndexError):  # a ValueError includes bad JSON or XML
        return [], status("drift", f"{sid} returned data honestcv could not read. {lost}; update honestcv.")
    except (FetchNotAllowed, HonestError) as exc:
        return [], status("error", f"{exc.message} {lost}.", code=getattr(exc, "http_status", None))
    problems = list(getattr(adapter, "problems", []))
    counts = http.stats.get(sid, {})
    if problems:
        return offers, status("error", "; ".join([_plural(len(offers)), *problems]), len(offers))
    if not offers:
        return [], status("empty", getattr(adapter, "empty_message", "the source returned no offers"))
    if counts.get("network", 0) == 0 and counts.get("cache", 0) > 0:
        return offers, status("cached", f"from cache, {_plural(len(offers))}", len(offers))
    return offers, status("ok", _plural(len(offers)), len(offers))
