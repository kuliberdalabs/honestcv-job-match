"""Offer import: files, pasted text, links, the inbox folder."""

import datetime as dt
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from honestcv.errors import HonestError
from honestcv.importers.eml import read_eml
from honestcv.importers.files import read_offer_file, read_text, text_offer
from honestcv.importers.jsonld import offers_from_html
from honestcv.importers.urls import finalize_offer, web_link
from honestcv.models import Offer, SourceStatus
from honestcv.state import SeenStore
from honestcv.workspace import Workspace

SUPPORTED_SUFFIXES = (".csv", ".eml", ".htm", ".html", ".json", ".md", ".txt")


def read_path(path: Path, today: dt.date) -> tuple[list[Offer], list[str]]:
    """Offers from one file, plus plain-English notes about rows or links that were skipped.

    Raises HonestError for a file type honestcv cannot read, or a file that does not parse.
    """
    path = Path(path).expanduser()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HonestError(f"honestcv cannot read {suffix or 'this kind of'} offer files.", path=path.name,
                          hint="Save the offer as .html or .txt, or copy its text and run: honestcv add-offer --paste")  # fmt: skip
    if not path.is_file():
        raise HonestError(f"The file {path.name} was not found.", path=path.name,
                          hint="If the file name has spaces, put it in quotes or drag the file into this window.")  # fmt: skip
    if suffix == ".eml":
        return read_eml(path)
    if suffix in (".htm", ".html"):
        return offers_from_html(read_text(path), source="import:html", attribution=f"your file {path.name}"), []
    return read_offer_file(path)


def import_path(path: Path, *, today: dt.date) -> list[Offer]:
    """Offers from a .csv, .json, .eml, .html, .txt or .md file."""
    return read_path(path, today)[0]


def import_text(text: str, *, title: str | None = None, company: str | None = None, url: str | None = None) -> Offer:
    """One offer from pasted text; the title is `title`, else the first heading or line."""
    return text_offer(text, title=title, company=company, url=web_link(url), source="import:text", attribution="your pasted text")


def import_url(url: str, *, title: str | None = None) -> Offer:
    """A thin offer that stores the link. The page is never fetched."""
    link = web_link(url)
    if not link:
        raise HonestError("That does not look like a web link.", hint="Paste the full address, starting with https://")
    host = urlsplit(link).hostname or link
    return finalize_offer(Offer(id="", source="import:text", attribution=f"your link to {host}", title=(title or "").strip() or host, url=link, thin=True))


def read_clipboard() -> str:
    """The clipboard text: pbpaste (macOS), PowerShell Get-Clipboard (Windows), wl-paste or xclip (Linux)."""
    if sys.platform == "darwin":
        commands = [["pbpaste"]]
    elif sys.platform.startswith("win"):
        commands = [["powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard"]]
    else:
        commands = [["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]]
    for command in commands:
        if shutil.which(command[0]):
            result = subprocess.run(command, capture_output=True, timeout=15, check=False)  # noqa: S603
            if result.returncode == 0:
                return result.stdout.decode("utf-8", errors="replace")
    raise HonestError("honestcv could not read the clipboard on this computer.",
                      hint="Save the offer text in a .txt file and run: honestcv add-offer FILE.txt")  # fmt: skip


def import_inbox(ws: Workspace, store: SeenStore, today: dt.date) -> tuple[list[Offer], SourceStatus]:
    """Offers from every file in offers/inbox/, on every scan. Files are tracked by SHA-256 and never moved.

    Notes about a file (skipped rows, unknown columns) are reported the first time the file is seen. A file
    that cannot be read makes the status `error`; other files still count.
    """
    files = sorted((p for p in ws.inbox.glob("*") if p.is_file() and not p.name.startswith(".")), key=lambda p: p.name) if ws.inbox.is_dir() else []
    if not files:
        return [], SourceStatus(id="inbox", state="empty", http_status=None, offers=0, message="no files in offers/inbox")
    offers: list[Offer] = []
    notes: list[str] = []
    broken: list[str] = []
    for path in files:
        fresh = True
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            fresh = not store.file_seen(digest)
            store.mark_file(digest)
            found, file_notes = read_path(path, today)
        except (HonestError, OSError) as exc:
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                broken.append(f"{path.name}: {getattr(exc, 'message', 'the file could not be read')}")
            elif fresh:
                notes.append(f"{path.name} was ignored (not a supported offer file)")
            continue
        offers += found
        notes += file_notes if fresh else []
    count = f"{len(files)} file{'s' * (len(files) != 1)}, {len(offers)} offer{'s' * (len(offers) != 1)}"
    message = "; ".join([count, *notes[:3], *broken[:3]])
    return offers, SourceStatus(id="inbox", state="error" if broken else "ok", http_status=None, offers=len(offers), message=message)
