"""HttpClient: honest User-Agent, host allow-list, robots.txt, min interval, TTL cache, retries."""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
import yaml

from honestcv.errors import FetchNotAllowed, HonestError, SourceRefused
from honestcv.textutil import safe_name
from honestcv.workspace import atomic_write

UNLISTED_HOST_PROBLEM = "honestcv only fetches from the job APIs listed in `honestcv sources`."
UNLISTED_HOST_FIX = "Save the page (Ctrl+S / Cmd+S) and run `honestcv add-offer page.html`, or copy the text and run `honestcv add-offer --paste`."
UNLISTED_HOST_MESSAGE = f"{UNLISTED_HOST_PROBLEM} {UNLISTED_HOST_FIX}"
RETRY_STATUSES = (429, 500, 502, 503, 504)
BACKOFF_SECONDS = (2, 4, 8)
ROBOTS_TTL_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 20.0


class SourceError(HonestError):
    """A source failed for a reason that is not a refusal: network trouble, HTTP 429 or 5xx after the retries."""

    exit_code = 3


class OfflineSkip(HonestError):
    """--offline and nothing for this request is in the cache."""


class RobotsUnreadable(HonestError):
    """robots.txt answered 5xx or could not be reached: the source is skipped for this run."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    text: str
    from_cache: bool


@cache
def load_registry() -> dict[str, dict]:
    """The sources registry (registry.yml), source id to entry."""
    text = resources.files("honestcv.sources").joinpath("registry.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["sources"]


def parse_robots(body: str, product: str = "honestcv") -> list[tuple[bool, str]]:
    """The (allow, path pattern) rules that apply to `product`: its own group if there is one, else the * group."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    for raw in body.splitlines():
        key, _, value = raw.split("#", 1)[0].partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif key in ("allow", "disallow"):
            rules.append((key == "allow", value))
    groups.append((agents, rules))
    own = [r for a, r in groups if any(x != "*" and x in product for x in a)]
    return [rule for group in (own or [r for a, r in groups if "*" in a]) for rule in group]


def robots_permits(rules: list[tuple[bool, str]], target: str) -> bool:
    """RFC 9309: the longest matching pattern wins, and Allow wins a tie. `*` and a final `$` are supported."""
    verdict = (-1, True)
    for allow, pattern in rules:
        regex = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\$", "$")
        if pattern and re.match(regex, target) and (len(pattern), allow) > verdict:
            verdict = (len(pattern), allow)
    return verdict[1]


class HttpClient:
    """GET-only client for the hosts in `allowed_hosts`. cache_dir gets http/ and robots/ folders.

    Tests put an httpx.MockTransport in `_transport` and replace `_sleep`, `_monotonic` and `_wall`.
    `stats[source_id]` counts network and cache answers; `last_status[source_id]` is the last HTTP status seen.
    """

    def __init__(self, cache_dir: Path, *, offline: bool, user_agent: str, allowed_hosts: frozenset[str]):
        self.cache_dir, self.offline, self.user_agent = Path(cache_dir), offline, user_agent
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self.stats: dict[str, dict[str, int]] = {}
        self.last_status: dict[str, int] = {}
        self._transport: httpx.BaseTransport | None = None
        self._client: httpx.Client | None = None
        self._sleep, self._monotonic, self._wall = time.sleep, time.monotonic, time.time
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, list[tuple[bool, str]] | HonestError] = {}

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---- public ----

    def get(self, source_id: str, url: str, *, params: dict | None = None) -> HttpResult:
        """The answer for url, from the cache within the source's TTL, else from the network.

        Raises FetchNotAllowed for a host outside the allow-list (before any socket use), OfflineSkip when
        offline with nothing cached, SourceRefused for HTTP 401/403/404 or a robots.txt Disallow, and
        SourceError for other failures. A returned result always has a 2xx status.
        """
        self._check_host(url)
        entry = load_registry().get(source_id, {})
        interval, ttl = float(entry.get("min_interval_s", 2)), float(entry.get("cache_ttl_h", 1)) * 3600
        full = url + ("?" + urlencode(params) if params else "")
        path = self.cache_dir / "http" / (hashlib.sha256(full.encode("utf-8")).hexdigest() + ".json")
        cached = self._read(path)
        if cached and (self.offline or self._wall() - cached["fetched_at"] < ttl):
            self._count(source_id, "cache", cached["status"])
            return HttpResult(cached["status"], cached["body"], True)
        if self.offline:
            raise OfflineSkip(f"{source_id} has nothing in the cache and honestcv is offline.")
        if not self.robots_allows(source_id, full):
            raise self._refused(source_id, None, f"{source_id} is not read: robots.txt disallows this path.")
        accept = str(entry.get("accept", "application/json"))
        for attempt in range(len(BACKOFF_SECONDS) + 1):
            response = self._send(source_id, full, accept, interval)
            if response.status_code not in RETRY_STATUSES or attempt == len(BACKOFF_SECONDS):
                break
            retry_after = response.headers.get("Retry-After", "").strip()
            self._sleep(min(int(retry_after), 60) if retry_after.isdigit() else BACKOFF_SECONDS[attempt])
        status = response.status_code
        if status in (401, 403, 404):
            raise self._refused(source_id, status, f"{source_id} refused access (HTTP {status}). honestcv does not work around this.")
        if status >= 300:
            error = SourceError(f"{source_id} answered HTTP {status}.", hint="Try again later, or import offers by file.")
            error.http_status = status  # type: ignore[attr-defined]
            raise error
        self._count(source_id, "network", status)
        self._write(path, {"url": full, "status": status, "content_type": response.headers.get("content-type", ""),
                           "fetched_at": self._wall(), "body": response.text})  # fmt: skip
        return HttpResult(status, response.text, False)

    def robots_allows(self, source_id: str, url: str) -> bool:
        """True when the host's robots.txt (cached 24 h) lets honestcv read this path.

        4xx means no rules. A 5xx or an unreachable robots.txt raises RobotsUnreadable: skip the source for this run.
        """
        self._check_host(url)
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host not in self._robots:
            self._robots[host] = self._load_robots(source_id, parts.scheme, host)
        rules = self._robots[host]
        if isinstance(rules, HonestError):
            raise rules
        return robots_permits(rules, (parts.path or "/") + (f"?{parts.query}" if parts.query else ""))

    # ---- internals ----

    def _check_host(self, url: str) -> None:
        if (urlsplit(url).hostname or "").lower() not in self.allowed_hosts:
            raise FetchNotAllowed(UNLISTED_HOST_PROBLEM, hint=UNLISTED_HOST_FIX)

    def _count(self, source_id: str, kind: str, status: int) -> None:
        self.stats.setdefault(source_id, {"network": 0, "cache": 0})[kind] += 1
        self.last_status[source_id] = status

    @staticmethod
    def _refused(source_id: str, status: int | None, message: str) -> SourceRefused:
        error = SourceRefused(message, hint="Try later, or import offers by file.")
        error.http_status = status  # type: ignore[attr-defined]
        return error

    def _send(self, source_id: str, url: str, accept: str, interval: float) -> httpx.Response:
        """One request (redirects are followed only inside the allow-list), at most one per `interval` seconds per source."""
        last = self._last_request.get(source_id)
        if last is not None and last + interval > self._monotonic():
            self._sleep(last + interval - self._monotonic())
        self._last_request[source_id] = self._monotonic()
        if self._client is None:
            self._client = httpx.Client(transport=self._transport, timeout=TIMEOUT_SECONDS, follow_redirects=False)
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        for _ in range(4):
            try:
                response = self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceError(f"{source_id} could not be reached ({type(exc).__name__}).",
                                  hint="Check your connection and try again, or import offers by file.") from None  # fmt: skip
            if response.is_redirect and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                self._check_host(url)
                continue
            return response
        raise SourceError(f"{source_id} sent too many redirects.")

    def _load_robots(self, source_id: str, scheme: str, host: str) -> list[tuple[bool, str]] | HonestError:
        product = self.user_agent.split("/", 1)[0].lower()
        path = self.cache_dir / "robots" / (safe_name(host) + ".json")
        cached = self._read(path)
        if cached and (self.offline or self._wall() - cached["fetched_at"] < ROBOTS_TTL_SECONDS):
            return parse_robots(cached["body"], product)
        if self.offline:
            return []
        try:
            response = self._send(source_id, f"{scheme}://{host}/robots.txt", "text/plain",
                                  float(load_registry().get(source_id, {}).get("min_interval_s", 2)))  # fmt: skip
        except SourceError:
            response = None
        if response is None or response.status_code >= 500:
            code = f"HTTP {response.status_code}" if response is not None else "not reachable"
            return RobotsUnreadable(f"{source_id}: robots.txt could not be read ({code}); skipped for this run.")
        body = response.text if response.status_code < 300 else ""
        self._write(path, {"status": response.status_code, "fetched_at": self._wall(), "body": body})
        return parse_robots(body, product)

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and "fetched_at" in data and "body" in data else None

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        try:
            atomic_write(path, json.dumps(data, ensure_ascii=False))
        except OSError:
            pass  # a cache that cannot be written only costs a request next time
