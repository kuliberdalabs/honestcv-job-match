"""Job-alert e-mails (stdlib email), with redaction.

Only offer fields are kept: never the sender, the recipients, the headers or body text that is not part of an offer.
"""

import email
import re
from email import policy
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path

from honestcv.importers.jsonld import job_postings, posting_to_offer
from honestcv.importers.urls import finalize_offer, web_link
from honestcv.models import Offer

_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+\d[\d\s().-]{6,}\d|\b\d{3}[ .-]\d{3}[ .-]\d{3}\b|\b\d{2}[ .-]\d{3}[ .-]\d{2}[ .-]\d{2}\b"
                    r"|\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")  # fmt: skip
_DROP_LINK = re.compile(r"unsubscribe|wypisz|zrezygnuj|ustawienia powiadomie|manage (?:your )?alerts?|view (?:this email|in (?:your )?browser)"
                        r"|privacy|polityka prywatno|regulamin", re.IGNORECASE)  # fmt: skip
_NOT_A_TITLE = re.compile(r"(?:view|see|show|open|more|apply|click|download|get|learn|help|contact|settings|log ?in|sign ?in|zobacz|wi[eę]cej|otw[oó]rz|pomoc)\b", re.IGNORECASE)
_TRACKER_HOST = re.compile(r"(?:click|clicks|link|links|track|tracking|trk|email|mail|redirect|go|url|e|em|mg|news|lnk|r|t)\d*\.|sendgrid|mandrillapp"
                           r"|list-manage|mailchi\.mp|mailgun|sparkpostmail|exacttarget", re.IGNORECASE)  # fmt: skip
_TRACKER_PATH = re.compile(r"/(?:r|l|c|u|click|clicks|track|redirect|ls/click|wf/click)(?:/|$)", re.IGNORECASE)
TRACKING_NOTE = " (tracking link from your e-mail)"


def redact(text: str) -> str:
    """Replace e-mail addresses and phone numbers with [removed]."""
    return _PHONE.sub("[removed]", _EMAIL.sub("[removed]", text))


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def _body(part: Message) -> str:
    try:
        return str(part.get_content())
    except (LookupError, UnicodeDecodeError, ValueError):
        return (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")


def _scrub(url: str | None) -> str | None:
    """The link without query parameters that carry an e-mail address (recipient tokens are common in alerts)."""
    if not url or "?" not in url:
        return url
    base, query = url.split("?", 1)
    kept = [seg for seg in query.split("&") if "@" not in seg and "%40" not in seg.lower()]
    return base + ("?" + "&".join(kept) if kept else "")


def _is_tracker(url: str) -> bool:
    host_and_path = re.match(r"https?://([^/?#]+)([^?#]*)", url)
    return bool(host_and_path and (_TRACKER_HOST.match(host_and_path.group(1)) or _TRACKER_PATH.match(host_and_path.group(2))))


def read_eml(path: Path) -> tuple[list[Offer], list[str]]:
    """Offers from one job-alert e-mail: JobPosting JSON-LD in the HTML part, plus the job links with their anchor text."""
    message = email.message_from_bytes(Path(path).read_bytes(), policy=policy.default)
    texts, pages = [], []
    for part in message.walk():
        kind = part.get_content_type()
        if kind in ("text/plain", "text/html") and part.get_content_disposition() != "attachment":
            (pages if kind == "text/html" else texts).append(_body(part))
    source, attribution = "import:eml", f"your file {Path(path).name}"
    offers = [o for page in pages for p in job_postings(re.findall(r"<script[^>]*ld\+json[^>]*>(.*?)</script>", page, re.S | re.I))
              if (o := posting_to_offer(p, source=source, attribution=attribution))]  # fmt: skip
    known = {o.url for o in offers}
    candidates: dict[str, str] = {}  # cleaned link -> longest anchor text
    pairs: list[tuple[str, str]] = []
    for page in pages:
        anchors = _Anchors()
        anchors.feed(page)
        pairs += anchors.links
    if not pages:
        for text in texts:
            lines = [line.strip() for line in text.splitlines()]
            pairs += [(line, lines[i - 1]) for i, line in enumerate(lines) if i and re.fullmatch(r"https?://\S+", line)]
    for href, label in pairs:
        link = _scrub(web_link(href))
        if link and link not in known and 3 <= len(label) <= 150 and re.search(r"[^\W\d_]", label) and not _DROP_LINK.search(label) \
                and not _NOT_A_TITLE.match(label) and len(label) > len(candidates.get(link, "")):  # fmt: skip
            candidates[link] = label
    for link, label in candidates.items():
        note = TRACKING_NOTE if _is_tracker(link) else ""
        offers.append(finalize_offer(Offer(id="", source=source, attribution=attribution + note, title=label, url=link, thin=True)))
    cleaned = [o.model_copy(update={"title": redact(o.title), "company": redact(o.company) if o.company else None,
                                    "location": redact(o.location) if o.location else None, "description": redact(o.description)}) for o in offers]  # fmt: skip
    notes = [] if cleaned else [f"{Path(path).name}: no job links or JobPosting data found"]
    return [finalize_offer(o) for o in cleaned], notes
