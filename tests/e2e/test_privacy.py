"""What leaves the computer: search terms come from the settings and the built-in skills list, never from CV free text."""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from honestcv import sources
from honestcv.config import load_config
from honestcv.pipeline import search_keywords
from honestcv.profile import load_profiles
from honestcv.workspace import Workspace, init_workspace

_spec = importlib.util.spec_from_file_location("fakenet", Path(__file__).resolve().parents[1] / "fixtures" / "sources" / "fakenet.py")
fakenet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakenet)

TODAY = ["--today", "2026-10-10"]
DAY = dt.date(2026, 10, 10)
PRIVATE = ("sick", "cancer", "treatment", "leave", "szpital", "kancelaria", "urlop")
CV = """# Maya Example

## Experience

Sick leave (cancer treatment), 2024-2026

2023-2025 Szpital Kliniczny im. Jana Pawła II - Analityk danych

Kancelaria Nowak i Wspólnicy, Asystentka, 2016-2019

### Data Analyst, Example Corp, 2019-2024
- Built reports in SQL.

## Skills
SQL, Excel
"""


@pytest.fixture
def private_workspace(tmp_path) -> Workspace:
    """A workspace whose only CV has dated lines that are not job titles, with the settings left as init wrote them."""
    cv = tmp_path / "cv.md"
    cv.write_text(CV, encoding="utf-8")
    init_workspace(tmp_path / "ws", cv=cv)
    ws = Workspace.for_root(tmp_path / "ws")
    ws.config.write_text(ws.config.read_text(encoding="utf-8").replace("sources: [himalayas, remoteok, weworkremotely, arbeitnow]", "sources: [himalayas]"), encoding="utf-8")
    return ws


def settings(ws: Workspace, **keys: str) -> None:
    """Replace the empty list of a settings line, for example target_titles='["data analyst"]'."""
    text = ws.config.read_text(encoding="utf-8")
    for key, value in keys.items():
        text = text.replace(f"{key}: []", f"{key}: {value}", 1)
    ws.config.write_text(text, encoding="utf-8")


def keywords(ws: Workspace) -> list[str]:
    cfg = load_config(ws.config)
    return search_keywords(cfg, load_profiles(ws, cfg, today=DAY))


def himalayas_requests(monkeypatch, ws: Workspace, cli) -> list[str]:
    """Run a scan with a fake network that answers Himalayas with no jobs; returns every URL that was requested."""
    net = fakenet.FakeNet({"/jobs/api/search": (200, '{"jobs": []}')})
    monkeypatch.setattr(sources, "HttpClient", lambda cache_dir, *, offline, user_agent, allowed_hosts: net.client(cache_dir, offline=offline))
    result = cli("scan", "--no-open", "--workspace", ws.root, *TODAY)
    assert result.exit_code == 0, result.output
    return [str(r.url) for r in net.requests if r.url.path != "/robots.txt"]


def test_cv_dated_lines_are_not_search_terms(private_workspace):
    profile = load_profiles(private_workspace, load_config(private_workspace.config), today=DAY)[0]
    assert any("sick leave" in title.lower() for title in profile.target_titles)  # the CV reader does take such lines as titles
    found = keywords(private_workspace)
    assert found and set(found) <= {"sql", "excel"}
    assert not any(word in k.lower() for k in found for word in PRIVATE)


def test_typed_titles_come_first_then_skills(private_workspace):
    settings(private_workspace, target_titles='["data analyst", "analityk danych"]')
    assert keywords(private_workspace) == ["data analyst", "analityk danych", "sql", "excel"]


def test_added_skills_are_kept_and_search_keywords_win(private_workspace):
    settings(private_workspace, skills_add='["dbt"]')
    assert keywords(private_workspace)[:1] == ["dbt"]
    settings(private_workspace, keywords='["python", "reporting"]')
    assert keywords(private_workspace) == ["python", "reporting"]


def test_no_skills_and_no_titles_sends_nothing(tmp_path):
    cv = tmp_path / "cv.md"
    cv.write_text("# Sam\n\n## Experience\n\nSick leave (cancer treatment), 2024-2026\n", encoding="utf-8")
    init_workspace(tmp_path / "ws", cv=cv)
    assert keywords(Workspace.for_root(tmp_path / "ws")) == []


def test_scan_requests_never_contain_cv_free_text(cli, monkeypatch, private_workspace):
    urls = himalayas_requests(monkeypatch, private_workspace, cli)
    assert "q=sql" in urls[0]
    assert not [u for u in urls if any(word in u.lower() for word in PRIVATE)]


def test_scan_requests_carry_the_typed_titles(cli, monkeypatch, private_workspace):
    settings(private_workspace, target_titles='["data analyst"]')
    urls = himalayas_requests(monkeypatch, private_workspace, cli)
    assert [u.split("q=")[1].split("&")[0] for u in urls[:3]] == ["data+analyst", "sql", "excel"]
    assert not [u for u in urls if any(word in u.lower() for word in PRIVATE)]


@pytest.mark.parametrize("name", ["README.md", "README.pl.md", "SECURITY.md"])
def test_the_pages_that_say_what_leaves_the_computer_agree_with_the_code(name):
    text = (Path(__file__).resolve().parents[2] / name).read_text(encoding="utf-8")
    for needle in ("Himalayas", "profile.skills_add", "profile.target_titles" if name == "README.pl.md" else "honestcv.yml", "search.keywords"):
        assert needle in text, (name, needle)
    assert "top 5" not in text and "derived from your CV" not in text and "No other CV data" not in text
