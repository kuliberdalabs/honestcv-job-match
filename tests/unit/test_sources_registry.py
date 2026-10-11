"""The registry (allow-list), docs/sources.md, and collect() over fake sources."""

import datetime as dt
import importlib
import importlib.util
import inspect
import pkgutil
from pathlib import Path

import pytest
import yaml

import honestcv.sources as sources
from honestcv.config import CompaniesConfig, Config, SearchConfig
from honestcv.errors import ConfigError
from honestcv.models import SourceStatus
from honestcv.sources import REGISTRY, USER_AGENT, allowed_hosts, collect, collection_status, registry_table

_spec = importlib.util.spec_from_file_location("fakenet", Path(__file__).resolve().parents[1] / "fixtures" / "sources" / "fakenet.py")
fakenet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakenet)
fixture = fakenet.fixture
REPO = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 10, 10)


def adapter_classes() -> dict[str, type]:
    found = {}
    for module in pkgutil.iter_modules(sources.__path__):
        if module.name in ("base", "http"):
            continue
        for _, cls in inspect.getmembers(importlib.import_module(f"honestcv.sources.{module.name}"), inspect.isclass):
            if cls.__module__.endswith(module.name) and hasattr(cls, "fetch") and getattr(cls, "id", ""):
                found[cls.id] = cls
    return found


def test_registry_consistency():
    adapters = adapter_classes()
    assert sorted(adapters) == sorted(REGISTRY) == sorted(sources.ADAPTERS)  # every adapter has an entry and every entry an adapter
    assert sorted(REGISTRY) == ["arbeitnow", "ashby", "greenhouse", "himalayas", "lever", "remoteok", "weworkremotely"]
    for source_id, entry in REGISTRY.items():
        assert entry["verdict"].startswith("SHIP_DEFAULT") and entry["name"] and entry["attribution"] and entry["notes"]
        assert entry["terms_url"].startswith("https://") and entry["verified_at"] == dt.date(2026, 10, 10)
        assert entry["min_interval_s"] > 0 and entry["cache_ttl_h"] > 0 and isinstance(entry["default_on"], bool)
        assert entry["hosts"] and all(h == h.lower() and "/" not in h for h in entry["hosts"]), source_id
        assert entry["endpoints"] and all(e.startswith("GET https://") for e in entry["endpoints"])  # nothing ever posts
        assert {e.split("/")[2] for e in entry["endpoints"]} <= set(entry["hosts"])
    assert allowed_hosts() == frozenset(h for e in REGISTRY.values() for h in e["hosts"]) and "api.eu.lever.co" in allowed_hosts()
    assert [i for i, e in REGISTRY.items() if e["default_on"]] == SearchConfig().sources  # the config default and the registry agree
    assert REGISTRY["ashby"]["endpoints"][0].endswith("?includeCompensation=true")
    assert (REGISTRY["remoteok"]["min_interval_s"], REGISTRY["remoteok"]["cache_ttl_h"], REGISTRY["himalayas"]["min_interval_s"], REGISTRY["himalayas"]["cache_ttl_h"]) == (1, 1, 2, 6)
    # no list of excluded hosts, no probe results, and none of the words the repository keeps out
    text = (REPO / "src" / "honestcv" / "sources" / "registry.yml").read_text(encoding="utf-8")
    assert set(yaml.safe_load(text)) == {"sources"}
    keys = {k for entry in REGISTRY.values() for k in entry}
    assert not {k for k in keys if any(w in k for w in ("exclu", "block", "deny", "banned", "probe", "disallow"))}
    banned = ("scr" + "ap", "byp" + "ass", "stea" + "lth", "undet" + "ect")
    assert not [w for w in banned if w in text.lower()]


def test_registry_table_and_docs():
    table = registry_table()
    for source_id, entry in REGISTRY.items():
        assert f"`{source_id}`" in table and entry["terms_url"] in table and "2026-10-10" in table
    assert table.count("\n") == len(REGISTRY) + 2 and "Last status" not in table and "off until companies are listed" in table
    statuses = [SourceStatus(id="remoteok", state="ok", http_status=200, offers=3, message="3 offers")]
    with_status = registry_table(statuses)
    assert "Last status" in with_status and "ok: 3 offers" in with_status and with_status.count("not run") == len(REGISTRY) - 1
    docs = (REPO / "docs" / "sources.md").read_text(encoding="utf-8")
    assert table in docs  # docs/sources.md is generated from the registry and cannot drift
    assert "\u2014" not in docs and "\u2013" not in docs


def route_everything():
    return {
        ("himalayas.app", "/jobs/api/search"): lambda r: (200, fixture("himalayas/search_page2.json" if r.url.params.get("cursor") else "himalayas/search_page1.json")),
        ("remoteok.com", "/api"): (200, fixture("remoteok/api.json")),
        ("weworkremotely.com", "/remote-jobs.rss"): (200, fixture("weworkremotely/feed.rss")),
        ("www.arbeitnow.com", "/api/job-board-api"): lambda r: (200, fixture(f"arbeitnow/page{r.url.params['page']}.json")),
        ("boards-api.greenhouse.io", "/v1/boards/tallgrove/jobs"): (200, fixture("greenhouse/board.json")),
    }


@pytest.fixture
def patched(monkeypatch):
    def use(net):
        def make(cache_dir, *, offline, user_agent, allowed_hosts):
            assert user_agent == USER_AGENT and "himalayas.app" in allowed_hosts
            return net.client(cache_dir, offline=offline, hosts=allowed_hosts)

        monkeypatch.setattr(sources, "HttpClient", make)
        return net

    return use


def test_collect(workspace, patched):
    net = patched(fakenet.FakeNet(route_everything()))
    cfg = Config(search=SearchConfig(keywords=["sql"], companies=CompaniesConfig(greenhouse=["tallgrove"])))
    offers, statuses = collect(cfg, workspace, offline=False, today=TODAY)
    assert [s.id for s in statuses] == ["himalayas", "remoteok", "weworkremotely", "arbeitnow", "greenhouse"]  # Greenhouse joins once companies are listed
    assert [s.state for s in statuses] == ["ok"] * 5 and [s.offers for s in statuses] == [3, 1, 2, 3, 2]
    assert statuses[1].message == "3 offers, 1 match your keywords" and len(offers) == 11
    assert [o.title for o in offers if o.source == "remoteok"] == ["Data Analyst"]  # RemoteOK has no search: keywords are applied here
    assert {r.url.host for r in net.requests} <= allowed_hosts() and collection_status(statuses) == "PASS"
    assert all(r.headers["user-agent"] == USER_AGENT for r in net.requests) and USER_AGENT == "honestcv/0.1 (+https://github.com/kuliberdalabs/honestcv-job-match)"
    assert (workspace.state_dir / "cache" / "http").is_dir() and (workspace.state_dir / "cache" / "robots").is_dir()
    # offline: the cache answers, even in a new run
    cached_offers, cached = collect(cfg, workspace, offline=True, today=TODAY)
    assert [s.state for s in cached] == ["cached"] * 5 and [o.id for o in cached_offers] == [o.id for o in offers] and "from cache" in cached[0].message


def test_collect_keywords_and_failures(workspace, patched, tmp_path):
    def fresh(name):
        return workspace.for_root(tmp_path / name)

    net = patched(fakenet.FakeNet(route_everything()))
    only_remoteok = SearchConfig(sources=["remoteok"], keywords=["haskell"])
    offers, (status,) = collect(Config(search=only_remoteok), fresh("a"), offline=False, today=TODAY)
    assert offers == [] and (status.state, status.offers, status.message) == ("empty", 0, "no offers matched your keywords")
    # no keywords: target titles are used for the search
    net = patched(fakenet.FakeNet(route_everything()))
    cfg = Config(profile={"target_titles": ["data analyst"]}, preferences={"countries": ["pl"]}, search=SearchConfig(sources=["himalayas"]))
    collect(cfg, fresh("b"), offline=False, today=TODAY)
    search_requests = [r for r in net.requests if r.url.path == "/jobs/api/search"]
    assert {r.url.params["q"] for r in search_requests} == {"data analyst"} and {r.url.params["country"] for r in search_requests} == {"PL"}
    # one source refused, one drifted, others fine: every status is reported and the run is PARTIAL
    routes = route_everything()
    routes[("weworkremotely.com", "/remote-jobs.rss")] = (403, "")
    routes[("www.arbeitnow.com", "/api/job-board-api")] = (200, fixture("arbeitnow/moved.json"))
    patched(fakenet.FakeNet(routes))
    offers, statuses = collect(Config(), fresh("c"), offline=False, today=TODAY)
    assert [s.state for s in statuses] == ["ok", "ok", "refused", "drift"] and len(offers) == 6
    assert "weworkremotely refused access (HTTP 403); results from it are missing" in statuses[2].message
    assert collection_status(statuses) == "PARTIAL"
    # offline with an empty cache: skipped, which does not make the run PARTIAL
    patched(fakenet.FakeNet(route_everything()))
    offers, statuses = collect(Config(), fresh("other"), offline=True, today=TODAY)
    assert offers == [] and {s.state for s in statuses} == {"skipped"} and collection_status(statuses) == "PASS"
    # a misspelled source is a config error, and nothing is requested
    net = patched(fakenet.FakeNet(route_everything()))
    with pytest.raises(ConfigError) as caught:
        collect(Config(search=SearchConfig(sources=["himalaya"])), fresh("d"), offline=False, today=TODAY)
    assert "himalaya" in caught.value.message and "remoteok" in caught.value.hint and net.requests == []
