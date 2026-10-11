"""Source adapters: synthetic payloads in, Offers out; a payload whose shape moved is drift, never "no matches"."""

import datetime as dt
import importlib.util
from pathlib import Path

from honestcv.config import CompaniesConfig, SearchConfig
from honestcv.sources import collection_status
from honestcv.sources.arbeitnow import ArbeitnowAdapter
from honestcv.sources.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from honestcv.sources.base import run_adapter
from honestcv.sources.himalayas import HimalayasAdapter
from honestcv.sources.remoteok import RemoteOkAdapter
from honestcv.sources.weworkremotely import WeWorkRemotelyAdapter

_spec = importlib.util.spec_from_file_location("fakenet", Path(__file__).resolve().parents[1] / "fixtures" / "sources" / "fakenet.py")
fakenet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakenet)
fixture = fakenet.fixture
TODAY = dt.date(2026, 10, 10)


def run(adapter, routes, tmp_path, search=None):
    net = fakenet.FakeNet(routes)
    offers, status = run_adapter(adapter, net.client(tmp_path / "cache"), search or SearchConfig(), TODAY)
    return net, offers, status


def himalayas_route(request):
    page = "search_page2.json" if request.url.params.get("cursor") else "search_page1.json"
    return 200, fixture(f"himalayas/{page}")


def ats_search(**companies):
    return SearchConfig(companies=CompaniesConfig(**companies))


def test_adapters_parse(tmp_path):
    cases = [
        (HimalayasAdapter(), {"/jobs/api/search": himalayas_route}, SearchConfig(), "Himalayas", 3),
        (RemoteOkAdapter(), {"/api": (200, fixture("remoteok/api.json"))}, SearchConfig(), "RemoteOK", 3),
        (WeWorkRemotelyAdapter(), {"/remote-jobs.rss": (200, fixture("weworkremotely/feed.rss"))}, SearchConfig(), "We Work Remotely", 2),
        (ArbeitnowAdapter(), {"/api/job-board-api": lambda r: (200, fixture(f"arbeitnow/page{r.url.params['page']}.json"))}, SearchConfig(), "Arbeitnow", 3),
        (GreenhouseAdapter(), {"/v1/boards/tallgrove/jobs": (200, fixture("greenhouse/board.json"))}, ats_search(greenhouse=["tallgrove"]), "careers page (Greenhouse)", 2),
        (LeverAdapter(), {"/v0/postings/quarrylane": (200, fixture("lever/postings.json"))}, ats_search(lever=["quarrylane"]), "quarrylane careers page (Lever)", 2),
        (AshbyAdapter(), {"/posting-api/job-board/harrowmere": (200, fixture("ashby/board.json"))}, ats_search(ashby=["harrowmere"]), "harrowmere careers page (Ashby)", 1),
    ]  # fmt: skip
    for adapter, routes, search, attribution, count in cases:
        _, offers, status = run(adapter, routes, tmp_path / adapter.id, search)
        assert status.state == "ok" and status.offers == count == len(offers), (adapter.id, status)
        assert status.message == f"{count} offer{'s' * (count != 1)}"
        for offer in offers:
            assert offer.title and offer.url and offer.url.startswith("https://")
            assert attribution in offer.attribution and offer.source == adapter.id
            assert offer.company and not offer.thin and len(offer.id) == 10
            assert "<" not in offer.description and "&lt;" not in offer.description
            assert "utm_" not in offer.url and "#" not in offer.url
        assert len({o.id for o in offers}) == count


def test_adapter_himalayas_fields(tmp_path):
    adapter = HimalayasAdapter(["PL"])
    net, offers, _ = run(adapter, {"/jobs/api/search": himalayas_route}, tmp_path, SearchConfig(keywords=["data analyst"]))
    first, second, third = offers
    assert [r.url.params.get("q") for r in net.requests if r.url.path == "/jobs/api/search"] == ["data analyst", "data analyst"]
    assert all(r.url.params.get("country") == "PL" for r in net.requests if r.url.path == "/jobs/api/search")
    assert net.requests[-1].url.params.get("cursor") == "cursor-page-2"  # the second page follows nextCursor
    assert first.title == "Data Analyst" and first.company == "Tallgrove Analytics" and first.remote == "remote"
    assert first.url == "https://himalayas.invalid/companies/tallgrove-analytics/jobs/data-analyst"
    assert first.apply_url == "https://himalayas.invalid/companies/tallgrove-analytics/jobs/data-analyst"  # utm_source removed
    assert (first.contract, first.level, first.countries, first.location) == (["employment"], "mid", ["PL", "SE"], "Poland, Sweden")
    assert (first.salary.min, first.salary.max, first.salary.currency, first.salary.unit) == (5200, 6400, "EUR", "month")
    assert first.posted_at == dt.date(2026, 10, 6) and first.valid_through == dt.date(2026, 11, 5)
    assert "SQL" in first.description and "- 3+ years of experience" in first.description
    assert (second.location, second.contract, second.level, second.salary, second.countries) == ("Worldwide", ["contract"], None, None, [])
    assert (third.location, third.countries, third.level) == ("6 countries", ["US", "CA", "MX", "BR", "CL"], "junior")
    assert (third.salary.min, third.salary.unit) == (40, "hour")


def test_adapter_himalayas_request_limits(tmp_path):
    net, _, _ = run(HimalayasAdapter(["PL", "SE"]), {"/jobs/api/search": himalayas_route}, tmp_path / "a", SearchConfig(keywords=["a", "b", "c", "d", "e"]))
    requests = [r for r in net.requests if r.url.path == "/jobs/api/search"]
    assert len(requests) == 6 and {r.url.params["q"] for r in requests} == {"a", "b", "c"}  # 3 keywords x 2 pages
    assert all("country" not in r.url.params for r in requests)  # two countries: no country filter
    net, _, _ = run(HimalayasAdapter(), {"/jobs/api/search": himalayas_route}, tmp_path / "b")  # no keywords: one plain search
    assert [r.url.params.get("q") for r in net.requests if r.url.path == "/jobs/api/search"] == [None, None]
    # no cursor in the answer: the older offset paging is used while totalCount says there is more
    calls = []

    def offsets(request):
        calls.append(request.url.params.get("offset"))
        return 200, fixture("himalayas/search_offset_page1.json" if not request.url.params.get("offset") else "himalayas/search_page2.json")

    run(HimalayasAdapter(), {"/jobs/api/search": offsets}, tmp_path / "c")
    assert calls == [None, "1"]


def test_adapter_remoteok_fields(tmp_path):
    _, offers, _ = run(RemoteOkAdapter(), {"/api": (200, fixture("remoteok/api.json"))}, tmp_path)
    assert [o.title for o in offers] == ["Data Analyst", "Platform Engineer", "Customer Support Agent"]  # element 0 is the legal notice
    first, second, _ = offers
    assert first.url == "https://remoteok.invalid/remote-jobs/remote-data-analyst-tallgrove-900001"
    assert first.apply_url == "https://remoteok.invalid/remote-jobs/remote-data-analyst-tallgrove-900001"  # utm_campaign removed
    assert (first.salary.min, first.salary.max, first.salary.currency, first.salary.unit) == (70000, 90000, None, None)
    assert first.location == "Worldwide" and first.remote == "remote" and first.posted_at == dt.date(2026, 10, 9)
    assert "Tags: analyst, sql, python" in first.description and "SQL" in first.description
    assert second.salary is None and second.location is None


def test_adapter_weworkremotely_fields(tmp_path):
    routes = {"/remote-jobs.rss": (200, fixture("weworkremotely/feed.rss")), "/categories/remote-data-jobs.rss": (200, fixture("weworkremotely/category.rss"))}
    _, offers, status = run(WeWorkRemotelyAdapter(), routes, tmp_path / "a", SearchConfig(weworkremotely_feeds=["remote-data-jobs"]))
    assert [o.title for o in offers] == ["Technical Customer Support", "Data Analyst: Delivery Reports", "Junior Reporting Analyst"]
    first, second, _ = offers
    assert first.company == "Mossby Support Services" and first.location == "Anywhere in the World" and first.countries == []
    assert first.contract == ["employment"] and first.posted_at == dt.date(2026, 10, 10) and first.valid_through == dt.date(2026, 11, 9)
    assert "Skills: Zendesk and Customer Support" in first.description and "- Zendesk" in first.description
    assert (second.countries, second.contract, second.location) == (["FI"], ["contract"], "Helsinki")
    assert "<b>" not in second.description and "operations" in second.description
    # a category feed that fails, or a bad feed name, is reported while the main feed still counts
    routes["/categories/remote-data-jobs.rss"] = (404, "")
    _, offers, status = run(WeWorkRemotelyAdapter(), routes, tmp_path / "b", SearchConfig(weworkremotely_feeds=["remote-data-jobs", "Bad Name"]))
    assert len(offers) == 2 and status.state == "error" and status.offers == 2
    assert "remote-data-jobs" in status.message and "Bad Name" in status.message


def test_adapter_arbeitnow_fields(tmp_path):
    routes = {"/api/job-board-api": lambda r: (200, fixture(f"arbeitnow/page{r.url.params['page']}.json"))}
    net, offers, _ = run(ArbeitnowAdapter(), routes, tmp_path)
    assert [r.url.params["page"] for r in net.requests if r.url.path.startswith("/api")] == ["1", "2"]  # page 2 only because page 1 has a next link
    first, second, third = offers
    assert (first.company, first.location, first.remote, first.contract) == ("Vossgrind Labs", "Berlin", None, ["employment"])
    assert (second.remote, second.contract, third.title) == ("remote", [], "Reporting Analyst")
    assert first.posted_at == dt.date(2026, 10, 10) and "SQL" in first.description
    net, offers, _ = run(ArbeitnowAdapter(), {"/api/job-board-api": (200, fixture("arbeitnow/page2.json"))}, tmp_path / "one")
    assert len(offers) == 1 and net.count("/api/job-board-api") == 1  # no next link: one request


def test_adapter_ats_fields(tmp_path):
    net, offers, _ = run(GreenhouseAdapter(), {"/v1/boards/tallgrove/jobs": (200, fixture("greenhouse/board.json"))}, tmp_path / "g", ats_search(greenhouse=["tallgrove"]))
    first, second = offers
    assert net.requests[-1].url.params["content"] == "true"
    assert (first.company, first.requisition_id, first.remote, first.location) == ("Tallgrove Analytics", "tallgrove:R-17", "remote", "Remote, Sweden")
    assert first.posted_at == dt.date(2026, 10, 2) and "- SQL and Python" in first.description
    assert (second.company, second.requisition_id, second.remote) == ("tallgrove", None, None)
    routes = {("api.lever.co", "/v0/postings/quarrylane"): (200, fixture("lever/postings.json")), ("api.eu.lever.co", "/v0/postings/quarrylane-eu"): (200, fixture("lever/postings.json"))}
    net, offers, _ = run(LeverAdapter(), routes, tmp_path / "l", ats_search(lever=["quarrylane"], lever_eu=["quarrylane-eu"]))
    assert {r.url.host for r in net.requests if r.url.path.startswith("/v0")} == {"api.lever.co", "api.eu.lever.co"}
    assert len(offers) == 2  # the same two postings from both hosts are merged by id inside the adapter
    first, second = offers
    assert (first.remote, first.countries, first.contract, first.location) == ("hybrid", ["SE"], ["employment"], "Malmo, Sweden")
    assert (first.salary.min, first.salary.max, first.salary.currency, first.salary.unit) == (45000, 52000, "SEK", "month")
    assert "Requirements" in first.description and "- SQL" in first.description and "We reply to every application." in first.description
    assert (second.contract, second.remote, second.salary) == (["internship"], None, None)
    net, offers, _ = run(AshbyAdapter(), {"/posting-api/job-board/harrowmere": (200, fixture("ashby/board.json"))}, tmp_path / "a", ats_search(ashby=["harrowmere"]))
    (only,) = offers  # the unlisted job is left out
    assert net.requests[-1].url.params["includeCompensation"] == "true"
    assert only.title == "Revenue Reporting Analyst" and (only.remote, only.countries, only.contract) == ("hybrid", ["SE"], ["employment"])
    assert (only.salary.min, only.salary.max, only.salary.currency, only.salary.unit) == (41000, 46000, "SEK", "month")
    assert only.location == "Gothenburg, Sweden, Remote (Sweden)" and "revenue reports" in only.description


def test_adapter_ats_failures_are_reported(tmp_path):
    routes = {"/v1/boards/tallgrove/jobs": (200, fixture("greenhouse/board.json")), "/v1/boards/missing/jobs": (404, "")}
    net, offers, status = run(GreenhouseAdapter(), routes, tmp_path / "a", ats_search(greenhouse=["tallgrove", "missing", "../escape"]))
    assert len(offers) == 2 and status.state == "error" and status.offers == 2
    assert "missing" in status.message and "../escape" in status.message and "refused access (HTTP 404)" in status.message
    assert not any("escape" in str(r.url) for r in net.requests)  # an invalid board name is never put into a URL
    net, offers, status = run(GreenhouseAdapter(), {}, tmp_path / "b", SearchConfig())
    assert (offers, status.state, net.requests) == ([], "empty", []) and "search.companies" in status.message


def test_drift_canary(tmp_path):
    cases = [
        (HimalayasAdapter(), {"/jobs/api/search": (200, fixture("himalayas/moved.json"))}, SearchConfig()),
        (RemoteOkAdapter(), {"/api": (200, fixture("remoteok/moved.json"))}, SearchConfig()),
        (RemoteOkAdapter(), {"/api": (200, fixture("remoteok/moved_entries.json"))}, SearchConfig()),  # most entries lack a title or link
        (WeWorkRemotelyAdapter(), {"/remote-jobs.rss": (200, fixture("weworkremotely/moved.rss"))}, SearchConfig()),
        (WeWorkRemotelyAdapter(), {"/remote-jobs.rss": (200, fixture("weworkremotely/doctype.rss"))}, SearchConfig()),  # DOCTYPE and entities are rejected
        (WeWorkRemotelyAdapter(), {"/remote-jobs.rss": (200, "<html>maintenance</html>")}, SearchConfig()),
        (ArbeitnowAdapter(), {"/api/job-board-api": (200, fixture("arbeitnow/moved.json"))}, SearchConfig()),
        (GreenhouseAdapter(), {"/v1/boards/tallgrove/jobs": (200, fixture("greenhouse/moved.json"))}, ats_search(greenhouse=["tallgrove"])),
        (LeverAdapter(), {"/v0/postings/quarrylane": (200, fixture("lever/moved.json"))}, ats_search(lever=["quarrylane"])),
        (AshbyAdapter(), {"/posting-api/job-board/harrowmere": (200, fixture("ashby/moved.json"))}, ats_search(ashby=["harrowmere"])),
        (RemoteOkAdapter(), {"/api": (200, "")}, SearchConfig()),  # a 200 with no body at all
        (RemoteOkAdapter(), {"/api": (200, "<html>Please wait</html>")}, SearchConfig()),
    ]  # fmt: skip
    statuses = []
    for i, (adapter, routes, search) in enumerate(cases):
        _, offers, status = run(adapter, routes, tmp_path / str(i), search)
        assert (offers, status.state, status.http_status) == ([], "drift", 200), (adapter.id, i, status)
        assert f"{adapter.id} returned data honestcv could not read" in status.message and "Results from it are missing" in status.message
        statuses.append(status)
    assert collection_status(statuses[:1]) == "FAIL"  # the only source drifted and there is no inbox: "Could not collect offers"
    ok = run(RemoteOkAdapter(), {"/api": (200, fixture("remoteok/api.json"))}, tmp_path / "ok")[2]
    assert collection_status([ok, statuses[0]]) == "PARTIAL" and collection_status([ok, statuses[0]], inbox_offers=3) == "PARTIAL"
    assert collection_status([statuses[0]], inbox_offers=3) == "PARTIAL" and collection_status([ok]) == "PASS"
    # a readable answer with nothing in it is `empty`, which is not a failure
    empty = run(HimalayasAdapter(), {"/jobs/api/search": (200, fixture("himalayas/search_empty.json"))}, tmp_path / "empty")[2]
    assert empty.state == "empty" and collection_status([empty]) == "PASS"
    refused = run(RemoteOkAdapter(), {"/api": (403, "")}, tmp_path / "refused")[2]
    assert (refused.state, refused.http_status) == ("refused", 403) and "refused access (HTTP 403); results from it are missing" in refused.message
    down = run(RemoteOkAdapter(), {"/api": (503, "")}, tmp_path / "down")[2]
    assert down.state == "error" and collection_status([down, ok]) == "PARTIAL"
    cached = run(RemoteOkAdapter(), {"/api": (200, fixture("remoteok/api.json"))}, tmp_path / "ok")  # second run, same cache folder
    assert cached[2].state == "cached" and cached[2].message == "from cache, 3 offers" and len(cached[1]) == 3
