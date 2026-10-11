"""A fake network for the source tests: it serves canned answers through httpx.MockTransport, records every
request and owns a fake clock, so no socket is ever opened and no test waits."""

from pathlib import Path

import httpx

from honestcv.sources import USER_AGENT, allowed_hosts
from honestcv.sources.http import HttpClient

FIXTURES = Path(__file__).parent


def fixture(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


class FakeNet:
    """routes: URL path (or (host, path)) -> (status, text[, headers]), a list of those (used in order, the last
    one repeats), or a function of the request. robots: robots.txt text, or a (status, text) pair; default is 404."""

    def __init__(self, routes: dict | None = None, robots: str | tuple | None = None):
        self.routes, self.robots = routes or {}, robots
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []
        self.sleeps: list[float] = []
        self.now, self.wall = 1000.0, 1_800_000_000.0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        self.wall += seconds

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(self.now)
        path = request.url.path
        if path == "/robots.txt":
            status, text = (404, "") if self.robots is None else self.robots if isinstance(self.robots, tuple) else (200, self.robots)
            return httpx.Response(status, text=text)
        route = self.routes.get((request.url.host, path), self.routes.get(path))
        if callable(route):
            route = route(request)
        if isinstance(route, list):
            route = route.pop(0) if len(route) > 1 else route[0]
        if route is None:
            return httpx.Response(404, text="not found")
        status, text, *rest = route
        return httpx.Response(status, text=text, headers=rest[0] if rest else {})

    def count(self, path: str) -> int:
        return sum(1 for r in self.requests if r.url.path == path)

    def client(self, cache_dir: Path, *, offline: bool = False, hosts: frozenset[str] | None = None) -> HttpClient:
        http = HttpClient(cache_dir, offline=offline, user_agent=USER_AGENT, allowed_hosts=hosts or allowed_hosts())
        http._transport = httpx.MockTransport(self.handler)
        http._sleep = self.sleep
        http._monotonic = lambda: self.now
        http._wall = lambda: self.wall
        return http
