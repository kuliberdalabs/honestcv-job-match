"""HttpClient: allow-list, retries, cache, min interval, robots.txt."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from honestcv.errors import FetchNotAllowed, SourceRefused
from honestcv.sources import collection_status
from honestcv.sources.base import run_adapter
from honestcv.sources.http import OfflineSkip, RobotsUnreadable, SourceError, parse_robots, robots_permits
from honestcv.sources.remoteok import RemoteOkAdapter
from honestcv.config import SearchConfig

_spec = importlib.util.spec_from_file_location("fakenet", Path(__file__).resolve().parents[1] / "fixtures" / "sources" / "fakenet.py")
fakenet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakenet)

API = "https://remoteok.com/api"


def warm(net, http):
    """Read robots.txt first and let the min interval pass, so a test sees only the sleeps it is about."""
    http.robots_allows("remoteok", API)
    net.now += 10


def test_http_no_retry_on_403(tmp_path):
    net = fakenet.FakeNet({"/api": (403, "forbidden")})
    http = net.client(tmp_path / "cache")
    warm(net, http)
    with pytest.raises(SourceRefused) as caught:
        http.get("remoteok", API)
    assert net.count("/api") == 1 and net.sleeps == []
    assert caught.value.http_status == 403 and "does not work around this" in caught.value.message
    for status in (401, 404):
        net = fakenet.FakeNet({"/api": (status, "")})
        with pytest.raises(SourceRefused):
            net.client(tmp_path / f"cache{status}").get("remoteok", API)
        assert net.count("/api") == 1


def test_http_retries_503_with_backoff(tmp_path):
    net = fakenet.FakeNet({"/api": (503, "down")})
    http = net.client(tmp_path / "cache")
    warm(net, http)
    with pytest.raises(SourceError) as caught:
        http.get("remoteok", API)
    assert net.count("/api") == 4  # the first try and 3 retries
    assert net.sleeps == [2, 4, 8] and caught.value.http_status == 503
    net = fakenet.FakeNet({"/api": [(503, ""), (503, ""), (200, "[]")]})
    http = net.client(tmp_path / "cache2")
    warm(net, http)
    assert http.get("remoteok", API).text == "[]" and net.sleeps == [2, 4]


def test_http_retry_after_is_honored_and_capped(tmp_path):
    net = fakenet.FakeNet({"/api": [(429, "", {"Retry-After": "5"}), (429, "", {"Retry-After": "500"}), (200, "[]")]})
    http = net.client(tmp_path / "cache")
    warm(net, http)
    http.get("remoteok", API)
    assert net.sleeps == [5, 60]


def test_min_interval_and_cache(tmp_path):
    net = fakenet.FakeNet({"/api": (200, '["a"]')})
    cache = tmp_path / "cache"
    http = net.client(cache)
    first = http.get("remoteok", API)
    assert (first.status, first.from_cache) == (200, False)
    again = http.get("remoteok", API)  # within the 1 h TTL: no request
    assert again.from_cache and again.text == '["a"]' and net.count("/api") == 1
    http.get("remoteok", API, params={"page": 2})  # a different URL is a real request, at least 1 s after the one before
    api_times = [t for r, t in zip(net.requests, net.times) if r.url.path == "/api"]
    assert api_times[1] - api_times[0] >= 1
    assert http.stats["remoteok"] == {"network": 2, "cache": 1}
    net.wall += 3601  # after the TTL the request is made again in full, without conditional headers
    net.now += 3601
    assert not http.get("remoteok", API).from_cache and net.count("/api") == 3
    assert "if-none-match" not in net.requests[-1].headers and "if-modified-since" not in net.requests[-1].headers
    assert net.requests[-1].headers["user-agent"] == "honestcv/0.1 (+https://github.com/kuliberdalabs/honestcv-job-match)"
    # a new client in a new run reads the same cache folder
    other = fakenet.FakeNet({"/api": (500, "")})
    assert other.client(cache).get("remoteok", API).from_cache and other.count("/api") == 0


def test_offline_serves_only_the_cache(tmp_path):
    net = fakenet.FakeNet({"/api": (200, "[]")})
    net.client(tmp_path / "cache").get("remoteok", API)
    net.wall += 10**6  # long after the TTL: offline still answers from the cache
    offline = net.client(tmp_path / "cache", offline=True)
    before = len(net.requests)
    assert offline.get("remoteok", API).from_cache
    with pytest.raises(OfflineSkip):
        offline.get("remoteok", API, params={"page": 9})
    assert len(net.requests) == before
    _, status = run_adapter(RemoteOkAdapter(), net.client(tmp_path / "empty", offline=True), SearchConfig(), net_today())
    assert status.state == "skipped"


def net_today():
    import datetime as dt

    return dt.date(2026, 10, 10)


def test_robots(tmp_path):
    cache = tmp_path / "cache"
    # a Disallow that covers the endpoint: the source is refused and the endpoint is never requested
    net = fakenet.FakeNet({"/api": (200, "[]")}, robots="User-agent: *\nDisallow: /api\n")
    offers, status = run_adapter(RemoteOkAdapter(), net.client(cache), SearchConfig(), net_today())
    assert (offers, status.state) == ([], "refused") and "robots.txt" in status.message
    assert net.count("/api") == 0 and net.count("/robots.txt") == 1
    # 404 and 401 mean no rules
    for code in (404, 401):
        net = fakenet.FakeNet({"/api": (200, "[]")}, robots=(code, "Disallow: /"))
        assert net.client(tmp_path / f"c{code}").get("remoteok", API).status == 200
    # 5xx: the source fails for the run (the run is PARTIAL), and robots.txt is not asked again
    net = fakenet.FakeNet({"/api": (200, "[]")}, robots=(503, ""))
    http = net.client(tmp_path / "c503")
    _, status = run_adapter(RemoteOkAdapter(), http, SearchConfig(), net_today())
    assert status.state == "error" and "robots.txt could not be read" in status.message and net.count("/api") == 0
    assert status.http_status == 503 and "Results from it are missing" in status.message and "skipped" not in status.message
    with pytest.raises(RobotsUnreadable):
        http.get("remoteok", API)
    assert net.count("/robots.txt") == 1
    # the answer is cached for 24 h
    net = fakenet.FakeNet({"/api": (200, "[]")}, robots="User-agent: *\nDisallow: /private\n")
    fresh = tmp_path / "cache24"
    net.client(fresh).get("remoteok", API)
    assert net.count("/robots.txt") == 1
    net.client(fresh).robots_allows("remoteok", API)
    assert net.count("/robots.txt") == 1
    net.wall += 24 * 3600 + 1
    net.client(fresh).robots_allows("remoteok", API)
    assert net.count("/robots.txt") == 2


def test_robots_rules_follow_rfc_9309():
    rules = parse_robots("User-agent: *\nDisallow: /\n\nUser-agent: honestcv\nAllow: /\nDisallow: /secret\n")
    assert robots_permits(rules, "/api") and not robots_permits(rules, "/secret/x")  # the honestcv group wins over *
    star = parse_robots("User-agent: *\nDisallow: /api\nAllow: /api/open # comment\nDisallow: /*?action=\nDisallow: /end$\n")
    assert not robots_permits(star, "/api/x") and robots_permits(star, "/api/open/x")  # the longest match wins
    assert not robots_permits(star, "/jobs?action=get_jobs") and robots_permits(star, "/jobs?page=2")
    assert not robots_permits(star, "/end") and robots_permits(star, "/end/more")
    assert robots_permits(parse_robots("User-agent: *\nDisallow:\n"), "/anything")  # an empty Disallow allows all
    assert robots_permits(parse_robots("User-agent: x\nDisallow: /\n"), "/api")  # no group for us, no rules


def test_unlisted_hosts_never_fetched(tmp_path):
    from honestcv.importers import import_url

    http = fakenet.FakeNet().client(tmp_path / "cache")
    http._transport = None  # the real transport would need a socket; the guard must stop the call before that
    for url in ("https://jobs.unlisted.invalid/offer/1", "http://localhost/x", "javascript:alert(1)", "https://remoteok.com.evil.invalid/api"):
        with pytest.raises(FetchNotAllowed) as caught:
            http.get("remoteok", url)
        assert "honestcv sources" in caught.value.message and "add-offer --paste" in caught.value.hint
    with pytest.raises(FetchNotAllowed):
        http.robots_allows("remoteok", "https://jobs.unlisted.invalid/offer/1")
    assert http._client is None  # no client, so no socket, was ever created
    offer = import_url("https://jobs.unlisted.invalid/offer/1?utm_source=mail", title="Data Analyst")
    assert (offer.thin, offer.url, offer.title) == (True, "https://jobs.unlisted.invalid/offer/1", "Data Analyst")
    # a redirect that leaves the allow-list is stopped too
    net = fakenet.FakeNet({"/api": (302, "", {"Location": "https://elsewhere.invalid/api"})})
    with pytest.raises(FetchNotAllowed):
        net.client(tmp_path / "cache2").get("remoteok", API)
    assert net.count("/api") == 1


def test_cache_files_hold_no_secrets_and_survive_junk(tmp_path):
    net = fakenet.FakeNet({"/api": (200, '["a"]')})
    http = net.client(tmp_path / "cache")
    http.get("remoteok", API)
    stored = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "cache" / "http").glob("*.json")]
    assert stored[0]["status"] == 200 and stored[0]["body"] == '["a"]'
    for p in (tmp_path / "cache" / "http").glob("*.json"):
        p.write_text("{not json", encoding="utf-8")  # a damaged cache file is ignored, not fatal
    net.wall += 1
    assert not http.get("remoteok", API).from_cache


def test_robots_unreadable_makes_the_run_partial(tmp_path):
    ok = run_adapter(RemoteOkAdapter(), fakenet.FakeNet({"/api": (200, "[]")}).client(tmp_path / "ok"), SearchConfig(), net_today())[1]
    down = run_adapter(RemoteOkAdapter(), fakenet.FakeNet({"/api": (200, "[]")}, robots=(503, "")).client(tmp_path / "down"), SearchConfig(), net_today())[1]
    assert (ok.state, down.state) == ("empty", "error") and collection_status([ok, down]) == "PARTIAL"
    assert collection_status([down]) == "FAIL"
    # robots.txt that cannot be reached at all fails the source the same way
    net = fakenet.FakeNet({"/api": (200, "[]")})

    def unreachable(request):
        net.requests.append(request)
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("no route", request=request)
        return httpx.Response(200, text="[]")

    http = net.client(tmp_path / "unreachable")
    http._transport = httpx.MockTransport(unreachable)
    _, status = run_adapter(RemoteOkAdapter(), http, SearchConfig(), net_today())
    assert status.state == "error" and "robots.txt could not be read (not reachable)" in status.message and net.count("/api") == 0
    assert collection_status([ok, status]) == "PARTIAL"


def test_redirect_to_another_source_is_not_followed(tmp_path):
    search = "https://himalayas.app/jobs/api/search"
    net = fakenet.FakeNet({("himalayas.app", "/jobs/api/search"): (302, "", {"Location": "https://remoteok.com/api?q=PRIVATE_CV_TITLE"})})
    with pytest.raises(SourceError) as caught:
        net.client(tmp_path / "cache").get("himalayas", search, params={"q": "PRIVATE_CV_TITLE"})
    assert caught.value.message == "himalayas redirected to another address; honestcv does not follow it."
    assert {r.url.host for r in net.requests} == {"himalayas.app"}  # not even robots.txt is asked on remoteok.com
    # through an adapter the source fails and the run is PARTIAL
    from honestcv.sources.himalayas import HimalayasAdapter

    ok = run_adapter(RemoteOkAdapter(), fakenet.FakeNet({"/api": (200, "[]")}).client(tmp_path / "ok"), SearchConfig(), net_today())[1]
    _, status = run_adapter(HimalayasAdapter(), net.client(tmp_path / "run"), SearchConfig(keywords=["PRIVATE_CV_TITLE"]), net_today())
    assert status.state == "error" and "redirected to another address" in status.message and "Results from it are missing" in status.message
    assert collection_status([ok, status]) == "PARTIAL" and {r.url.host for r in net.requests} == {"himalayas.app"}


@pytest.mark.parametrize("location", [
    "http://remoteok.com/api2",  # https to http on the same host
    "https://remoteok.com/private",  # a path robots.txt disallows
    "https://remoteok.com:8443/api2",  # a port other than 443
    "https://reader" + "@remoteok.com/api2",  # user info
    "https://remoteok.com:99999/api2",  # a port that is not a port
    "https://[/api2",  # an address that cannot be read
])  # fmt: skip
def test_redirect_that_breaks_a_rule_is_not_followed(tmp_path, location):
    net = fakenet.FakeNet({"/api": (301, "", {"Location": location}), "/api2": (200, "[]"), "/private": (200, "[]")},
                          robots="User-agent: *\nDisallow: /private\n")  # fmt: skip
    with pytest.raises(SourceError) as caught:
        net.client(tmp_path / "cache").get("remoteok", API)
    assert caught.value.message == "remoteok redirected to another address; honestcv does not follow it."
    assert [r.url.path for r in net.requests] == ["/robots.txt", "/api"]  # the target is never requested


def test_redirect_on_the_same_host_is_followed(tmp_path):
    net = fakenet.FakeNet({"/api": (301, "", {"Location": "/api/v2"}), "/api/v2": (308, "", {"Location": "https://remoteok.com:443/api/v3"}),
                           "/api/v3": (200, '["moved"]')})  # fmt: skip
    http = net.client(tmp_path / "cache")
    warm(net, http)
    assert http.get("remoteok", API).text == '["moved"]'
    hops = [(r.url.path, t) for r, t in zip(net.requests, net.times) if r.url.path.startswith("/api")]
    assert [p for p, _ in hops] == ["/api", "/api/v2", "/api/v3"] and all(r.url.scheme == "https" for r in net.requests)
    assert all(b[1] - a[1] >= 1 for a, b in zip(hops, hops[1:]))  # the min interval holds for every hop
    # 3 redirects are followed; a 4th is not
    chain = {f"/r{i}": (302, "", {"Location": f"/r{i + 1}"}) for i in range(4)} | {"/r3": (200, "[]")}
    assert fakenet.FakeNet(chain).client(tmp_path / "three").get("remoteok", "https://remoteok.com/r0").text == "[]"
    chain["/r3"] = (302, "", {"Location": "/r4"})
    longer = fakenet.FakeNet(chain | {"/r4": (200, "[]")})
    with pytest.raises(SourceError) as caught:
        longer.client(tmp_path / "four").get("remoteok", "https://remoteok.com/r0")
    assert "too many redirects" in caught.value.message and longer.count("/r4") == 0


def test_no_cookie_is_kept_or_sent(tmp_path):
    jar_cookie = {"Set-Cookie": "session=server-value; Path=/; Domain=remoteok.com"}
    net = fakenet.FakeNet({"/api": (200, "[]", jar_cookie), "/next": (302, "", {"Location": "/api", **jar_cookie})})
    http = net.client(tmp_path / "cache")
    http.get("remoteok", API)
    http.get("remoteok", API, params={"page": 2})
    http.get("remoteok", "https://remoteok.com/next")  # a cookie set on a redirect is not sent to the next hop either
    assert len(net.requests) == 5 and all("cookie" not in r.headers for r in net.requests)
    assert list(http._client.cookies.jar) == []


def test_response_size_is_capped(tmp_path):
    big = "x" * (15 * 1024 * 1024 + 1)
    net = fakenet.FakeNet({"/api": (200, big), "/fits": (200, "[" + " " * (15 * 1024 * 1024 - 2) + "]")})
    http = net.client(tmp_path / "cache")
    with pytest.raises(SourceError) as caught:
        http.get("remoteok", API)
    assert "more than 15 MB" in caught.value.message
    assert len(http.get("remoteok", "https://remoteok.com/fits").text) == 15 * 1024 * 1024  # exactly at the limit is read
    _, status = run_adapter(RemoteOkAdapter(), fakenet.FakeNet({"/api": (200, big)}).client(tmp_path / "run"), SearchConfig(), net_today())
    assert status.state == "error" and "more than 15 MB" in status.message
