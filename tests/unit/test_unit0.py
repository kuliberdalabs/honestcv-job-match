"""Contracts, shared helpers, samples and golden fixtures."""

import datetime as dt
import importlib
import json
import os
import re
import socket
import time
import unicodedata
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from honestcv import errors, textutil, workspace
from honestcv.config import Config, ScoringConfig, config_digest, default_config_text, load_config, validate_scoring
from honestcv.errors import ConfigError, HonestError, WorkspaceError
from honestcv.models import Ledger, LedgerIndex, Offer, Preferences, RuleCode, Salary, ScanRun, Severity
from honestcv.textutil import durations, hit, normalize, numbers, safe_name, sentences

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "src" / "honestcv" / "samples" / "en"
GOLDEN = REPO / "tests" / "golden"
HEADER = "Fictional sample. Any resemblance to real people or companies is accidental."


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ network guard


def test_sockets_are_blocked():
    with pytest.raises(RuntimeError):
        socket.socket()
    with pytest.raises(RuntimeError):
        socket.create_connection(("example.invalid", 80))
    with pytest.raises(RuntimeError):
        socket.getaddrinfo("example.invalid", 80)


# ------------------------------------------------------------------ hit()


@pytest.mark.parametrize(
    "text, token, expected",
    [
        ("automation engineer", "auto*", True),
        ("we automate reports", "auto*", True),
        ("a semiautomatic tool", "auto*", False),
        ("sales manager", "sales*", True),
        ("salesforce admin", "sales*", True),
        ("presales lead", "sales*", False),
        ("python developer", "python", True),
        ("pythonic code", "python", False),
        ("cpython internals", "python", False),
        ("senior platform engineer", "platform engineer", True),
        ("platform\nengineer", "platform engineer", True),
        ("platform engineering", "platform engineer", False),
        ("asp.net core", ".net", True),
        ("dotnet", ".net", False),
        ("c++ and c#", "c++", True),
        ("Python is great", "PYTHON", True),
        ("analityk danych w łodzi", "łodzi", True),
        ("e-commerce retail", "e-commerce", True),
        ("logistics hub", "logistic*", True),
        ("anything", "", False),
    ],
)
def test_hit_token_semantics(text, token, expected):
    assert hit(text.lower(), token) is expected


# ------------------------------------------------------------------ normalize() and plain()


def test_normalize_ligatures_soft_hyphen_quotes_and_polish_letters():
    assert normalize("\ufb01le \ufb02ow") == "file flow"
    assert normalize("an\u00adalyst") == "analyst"
    assert normalize("\u201cquoted\u201d and \u2018single\u2019 \u201elow\u201d") == "\"quoted\" and 'single' \"low\""
    decomposed = unicodedata.normalize("NFD", "Zażółć gęślą jaźń")
    assert decomposed != "Zażółć gęślą jaźń" and normalize(decomposed) == "Zażółć gęślą jaźń"
    assert normalize("a\u00a0b") == "a b"
    assert normalize("line\r\nnext\rlast") == "line\nnext\nlast"


def test_normalize_joins_line_end_hyphenation_only_for_lowercase_continuations():
    assert normalize("data analy-\nsis") == "data analysis"
    assert normalize("analy- \n  sis") == "analysis"
    assert normalize("skills:\n- SQL\n- Python") == "skills:\n- SQL\n- Python"
    assert normalize("self-\nService") == "self-\nService"
    assert normalize("ticket-backlog") == "ticket-backlog"


def test_plain_turns_html_into_lines():
    html = "<html><head><style>p{}</style></head><body><h2>Requirements</h2><ul><li>SQL</li><li>3+ years</li></ul><p>Fluent&nbsp;English&amp;Swedish</p><script>x()</script></body></html>"
    text = textutil.plain(html)
    assert text.splitlines() == ["Requirements", "- SQL", "- 3+ years", "Fluent English&Swedish"]
    assert textutil.plain("AT&amp;T ships \ufb01les") == "AT&T ships files"


# ------------------------------------------------------------------ tokenize() and stem()


def test_tokenize_splits_hyphens_and_drops_stopwords():
    assert textutil.tokenize("I deployed our churn model to production and it cut churn by 12%.") == [
        "deployed", "churn", "model", "production", "cut", "churn", "12",
    ]  # fmt: skip
    assert textutil.tokenize("ticket-backlog dashboard") == ["ticket", "backlog", "dashboard"]
    assert textutil.tokenize("Maya's team") == ["maya", "team"]
    assert textutil.tokenize("Nie kierowałam jeszcze zespołem.") == ["kierowałam", "zespołem"]
    for word in ("have", "not", "i", "a", "the", "of", "our", "across", "over", "all"):
        assert word in textutil.stopwords("en")
    for word in ("experience", "team", "support", "data", "analyst", "time"):
        assert word not in textutil.stopwords()


@pytest.mark.parametrize(
    "a, b",
    [
        ("teams", "team"),
        ("analysts", "analyst"),
        ("times", "time"),
        ("dashboards", "dashboard"),
        ("companies", "company"),
        ("processes", "process"),
        ("courses", "course"),
        ("planned", "plans"),
        ("reduced", "reduce"),
        ("making", "make"),
        ("działów", "działy"),
        ("działach", "dział"),
        ("rekrutacji", "rekrutacja"),
        ("zespołem", "zespołu"),
    ],
)
def test_stem_brings_inflections_together(a, b):
    assert textutil.stem(a) == textutil.stem(b)


def test_stem_leaves_short_words_numbers_and_distinct_words_apart():
    assert textutil.stem("use") == "use"
    assert textutil.stem("2026") == "2026"
    assert textutil.stem("status") == "status"
    assert textutil.stem("analysis") == "analysis"
    assert textutil.stem("feeds") == "feed"
    assert textutil.stem("data") != textutil.stem("date")


# ------------------------------------------------------------------ numbers()


def _nums(text):
    return [(n.value, n.has_plus, n.is_year, n.is_percent, n.is_vague) for n in numbers(text)]


def test_numbers_digits_percent_plus_years():
    assert _nums("it cut churn by 12%.") == [(12.0, False, False, True, False)]
    assert _nums("a team of 4 analysts for all 9 support teams") == [(4.0, False, False, False, False), (9.0, False, False, False, False)]
    assert _nums("3+ years, 2016-2020") == [(3.0, True, False, False, False), (2016.0, False, True, False, False), (2020.0, False, True, False, False)]
    assert _nums("Over 12 years") == [(12.0, True, False, False, False)]
    assert _nums("more than 300 users, ponad 40 osób") == [(300.0, True, False, False, False), (40.0, True, False, False, False)]


def test_numbers_separators_and_suffixes():
    values = [n.value for n in numbers("40,000 SEK, 12 000 PLN, 3,5 roku, 2.5 days, 4.500 zł, 40k, 2x faster")]
    assert values == [40000.0, 12000.0, 3.5, 2.5, 4500.0, 40000.0, 2.0]
    assert numbers("12 percent")[0].is_percent and numbers("25 procent")[0].is_percent


def test_numbers_ignore_identifiers_and_ordinals():
    assert numbers("C1, B2B, Python3, COM1, 1st, 3D") == []


def test_numbers_dates_count_as_years():
    found = numbers("handed over 2024-02-12, started 03/2019")
    assert [(n.value, n.is_year, n.raw) for n in found] == [(2024.0, True, "2024-02-12"), (2019.0, True, "03/2019")]


def test_numbers_number_words_en_and_pl():
    assert [n.value for n in numbers("three support teams")] == [3.0]
    assert [n.value for n in numbers("twenty-five tickets, a hundred users, a dozen shops")] == [25.0, 100.0, 12.0]
    assert [n.value for n in numbers("dla trzech działów, trzy zespoły, z trzema osobami")] == [3.0, 3.0, 3.0]
    assert [n.value for n in numbers("dwóch działów, pięć lat, dwanaście sklepów")] == [2.0, 5.0, 12.0]
    assert numbers("someone, often, tone") == []


def test_numbers_polish_ten_is_a_pronoun_in_polish_text():
    assert numbers("ten projekt był ważny") == []
    assert [n.value for n in numbers("ten teams")] == [10.0]


def test_numbers_vague_quantities():
    for text in ("dozens of teams", "hundreds of tickets", "several stores", "many clients", "numerous users",
                 "multiple teams", "setki zgłoszeń", "dziesiątki firm", "kilkanaście sklepów", "kilkunastu klientów",
                 "kilkadziesiąt działów", "wiele firm", "liczne zespoły"):  # fmt: skip
        found = numbers(text)
        assert len(found) == 1 and found[0].is_vague and found[0].value == -1.0, text


def test_numbers_report_start_offsets():
    text = "Led a team of 4 analysts"
    (n,) = numbers(text)
    assert text[n.start : n.start + len(n.raw)] == "4"


# ------------------------------------------------------------------ durations()


def _dur(text):
    return [(d.raw, d.years, d.start_year, d.end_year, d.has_over) for d in durations(text)]


def test_durations_counts():
    assert _dur("15 years") == [("15 years", 15.0, None, None, False)]
    assert _dur("Over 12 years of customer support") == [("Over 12 years", 12.0, None, None, True)]
    assert _dur("12+ years") == [("12+ years", 12.0, None, None, True)]
    assert _dur("ponad 10 lat") == [("ponad 10 lat", 10.0, None, None, True)]
    assert _dur("3-5 years") == [("3-5 years", 5.0, None, None, False)]
    assert _dur("trzy lata") == [("trzy lata", 3.0, None, None, False)]
    assert _dur("18 months") == [("18 months", 1.5, None, None, False)]
    assert _dur("over a year") == [("over a year", 1.0, None, None, True)]


def test_durations_ranges():
    assert _dur("2010-2025") == [("2010-2025", 15.0, 2010, 2025, False)]
    assert _dur("from 2021 to 2026") == [("from 2021 to 2026", 5.0, 2021, 2026, False)]
    assert _dur("2016\u20132020") == [("2016\u20132020", 4.0, 2016, 2020, False)]
    assert _dur("03/2019 - present") == [("03/2019 - present", None, 2019, None, False)]
    assert _dur("od 2019 do obecnie") == [("od 2019 do obecnie", None, 2019, None, False)]
    assert _dur("od 2019") == [("od 2019", None, 2019, None, False)]
    assert _dur("since 2019") == [("since 2019", None, 2019, None, False)]
    assert _dur("Mar 2019 - Jun 2021")[0][1:4] == (2.25, 2019, 2021)


def test_durations_ignore_single_years_and_plain_numbers():
    assert durations("w 2016 roku") == []
    assert durations("from 41 to 18") == []
    assert durations("ten rok") == []


# ------------------------------------------------------------------ sentences()


def test_sentences_planted_letter_line_numbers():
    text = (SAMPLES / "letters" / "cover-letter-planted.md").read_text(encoding="utf-8")
    units = sentences(text)
    assert [no for no, _ in units] == [3, 5, 7, 9, 11, 13, 14]
    assert units[0] == (3, "I deployed our churn model to production and it cut churn by 12%.")
    assert units[-2:] == [(13, "Kind regards,"), (14, "Maya Lindqvist")]


def test_sentences_skip_comment_lines_headings_and_rules():
    text = "\n".join([
        "<!-- comment only -->",
        "# Heading",
        "---",
        "<!-- a comment",
        "over two lines -->",
        "Real sentence one. Real sentence two!",
        "| --- | --- |",
    ])  # fmt: skip
    assert sentences(text) == [(6, "Real sentence one."), (6, "Real sentence two!")]


def test_sentences_bullets_keep_markers_and_wrapped_lines():
    text = "\n".join([
        "## Highlights",
        "- Built the weekly dashboard. <!-- c:backlog-dashboard -->",
        "* A bullet that wraps",
        "  onto a second line.",
        "1. A numbered item",
        "Paragraph one, e.g. with an abbreviation. Mr. Example agreed.",
        "A wrapped para-",
        "graph line.",
    ])  # fmt: skip
    assert sentences(text) == [
        (2, "Built the weekly dashboard. <!-- c:backlog-dashboard -->"),
        (3, "A bullet that wraps onto a second line."),
        (5, "A numbered item"),
        (6, "Paragraph one, e.g. with an abbreviation."),
        (6, "Mr. Example agreed."),
        (7, "A wrapped paragraph line."),
    ]


def test_sentences_report_the_line_where_a_sentence_starts():
    text = "First sentence on line one. Second one starts here and\ncontinues on line two. Third on line two."
    assert [no for no, _ in sentences(text)] == [1, 1, 2]


# ------------------------------------------------------------------ snippet, safe_name, home_relative


def test_snippet_width_and_ellipsis():
    text = "word " * 100
    s = textutil.snippet(text, 200, 210)
    assert len(s) <= 120 and s.startswith("...") and s.endswith("...")
    assert textutil.snippet("short text", 0, 5) == "short text"
    assert len(textutil.snippet("x" * 500, 0, 400)) == 120


def test_safe_name():
    assert safe_name("Specjalista ds. kadr (k/m)") == "specjalista-ds-kadr-k-m"
    assert safe_name("Łódź: Analityk Danych") == "lodz-analityk-danych"
    assert safe_name("2026-10-10 Tallgrove Analytics / Data Analyst") == "2026-10-10-tallgrove-analytics-data-analyst"
    for reserved in ("CON", "nul", "Com1", "LPT9", "aux", "PRN"):
        assert safe_name(reserved) not in {"con", "prn", "aux", "nul", "com1", "lpt9"}
    assert safe_name("") == "untitled"
    assert safe_name("!!!") == "untitled"
    long = safe_name("a very long job title " * 10)
    assert len(long) <= 60 and not long.endswith("-")
    assert re.fullmatch(r"[a-z0-9-]+", safe_name("Zażółć gęślą jaźń ™ ½"))


def test_home_relative(_isolated_home):
    home = Path.home()
    assert textutil.home_relative(home / "honestcv" / "cvs") == "~/honestcv/cvs"
    assert textutil.home_relative(home) == "~"
    assert textutil.home_relative(Path("cvs/cv.pdf")) == "cvs/cv.pdf"
    outside = Path(os.path.abspath(os.sep)) / "srv-example-outside"
    assert textutil.home_relative(outside) == str(outside)


# ------------------------------------------------------------------ errors


def test_error_exit_codes_and_fields():
    assert errors.HonestError("x").exit_code == 1
    assert errors.ConfigError("x").exit_code == 2
    assert errors.LedgerError("x").exit_code == 2
    assert errors.WorkspaceError("x").exit_code == 1
    assert errors.SourceRefused("x").exit_code == 3
    assert errors.SourceDrift("x").exit_code == 3
    assert errors.FetchNotAllowed("x").exit_code == 1
    err = ConfigError("bad", hint="fix it", path="honestcv.yml", line=4)
    assert (str(err), err.hint, err.where()) == ("bad", "fix it", "honestcv.yml line 4")


def test_validation_messages_never_include_input_values():
    secret = "Confidential CV line about Example Bank"
    with pytest.raises(Exception) as info:
        Ledger.model_validate({"schema_version": 1, "person": {}, "claims": [{"id": "x", "kind": secret}]})
    messages = errors.validation_messages(info.value)
    assert messages and all(secret not in text for _, text in messages)
    assert messages[0][0] == ("claims", 0, "kind")


# ------------------------------------------------------------------ config


def test_load_config_none_gives_defaults():
    cfg = load_config(None)
    assert isinstance(cfg, Config)
    assert [c.id for c in cfg.scoring.criteria] == ["skills", "title", "level", "location", "salary", "contract", "language"]
    assert sum(c.weight for c in cfg.scoring.criteria) == 100
    assert cfg.output.csv_delimiter == ","
    assert cfg.demo_today is None
    assert cfg.scoring.surface.min_match("DOWNSHIFT") == 45


def test_default_config_text_parses_to_the_defaults(tmp_path):
    path = tmp_path / "honestcv.yml"
    path.write_text(default_config_text(), encoding="utf-8")
    cfg = load_config(path)
    assert cfg == Config()
    assert config_digest(cfg) == config_digest(Config())
    assert "output:" in default_config_text() and "csv_delimiter" in default_config_text()


def test_missing_and_empty_config_give_defaults(tmp_path):
    assert load_config(tmp_path / "absent.yml") == Config()
    empty = tmp_path / "honestcv.yml"
    empty.write_text("# only a comment\n", encoding="utf-8")
    assert load_config(empty) == Config()
    nulls = tmp_path / "nulls.yml"
    nulls.write_text("profile:\npreferences:\n  remote:\n", encoding="utf-8")
    assert load_config(nulls) == Config()


def test_demo_today_accepted(tmp_path):
    path = tmp_path / "honestcv.yml"
    path.write_text("demo_today: 2026-10-10\n", encoding="utf-8")
    assert load_config(path).demo_today == dt.date(2026, 10, 10)


def test_weight_validator_message_and_line(tmp_path):
    text = default_config_text().replace("weight: 45", "weight: 60")
    path = tmp_path / "honestcv.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(path)
    err = info.value
    assert err.message == "scoring.criteria weights sum to 115; they must sum to 100. Lower some weights by 15 in total."
    assert err.line == text.splitlines().index("  criteria:              # weights must sum to 100") + 1
    assert err.exit_code == 2 and err.hint


def test_weight_validator_raise_message():
    cfg = ScoringConfig(criteria=[{"id": "skills", "type": "skills_coverage", "weight": 90}])
    with pytest.raises(ConfigError, match="sum to 90; they must sum to 100. Raise some weights by 10 in total."):
        validate_scoring(cfg)


@pytest.mark.parametrize(
    "old, new, expected",
    [
        ("type: salary,", "type: salry,", "has type 'salry', which is not a known type"),
        ("{id: contract, type: contract,        weight: 5}", "{id: skills, type: contract, weight: 5}", "uses the id 'skills' a second time"),
        ("{code: hard_requirement_missing, points: -10}", "{code: hard_requirement_missing, points: 10}", "has points 10; penalty points must be 0 or below"),
    ],
)
def test_scoring_errors_carry_line_numbers(tmp_path, old, new, expected):
    text = default_config_text()
    assert old in text
    text = text.replace(old, new)
    path = tmp_path / "honestcv.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(path)
    assert expected in info.value.message
    assert info.value.line == next(i for i, line in enumerate(text.splitlines(), 1) if new in line)


def test_negative_weight_is_an_error():
    cfg = ScoringConfig(criteria=[
        {"id": "skills", "type": "skills_coverage", "weight": 105},
        {"id": "title", "type": "title_match", "weight": -5},
    ])  # fmt: skip
    with pytest.raises(ConfigError, match="weights cannot be negative"):
        validate_scoring(cfg)


def test_value_errors_show_the_line_and_an_example(tmp_path):
    path = tmp_path / "honestcv.yml"
    path.write_text("profile:\n  level: mid\npreferences:\n  remote: remote\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(path)
    assert info.value.message.startswith("'remote' must be a list, for example: remote: [remote, hybrid]")
    assert info.value.line == 4


def test_unknown_keys_and_yaml_errors(tmp_path):
    path = tmp_path / "honestcv.yml"
    path.write_text("preference:\n  remote: [remote]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="did you mean 'preferences'") as info:
        load_config(path)
    assert info.value.line == 1
    path.write_text("profile:\n  level: mid\n   years: 3\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(path)
    assert info.value.line == 3 and "could not be read" in info.value.message


def test_country_code_no_stays_text_and_codes_are_upper_case(tmp_path):
    path = tmp_path / "honestcv.yml"
    path.write_text("preferences:\n  countries: [NO, se]\n  min_salary: [{amount: 12000, currency: pln}]\n", encoding="utf-8")
    prefs = load_config(path).preferences
    assert prefs.countries == ["NO", "SE"]
    assert [(m.currency, m.unit) for m in prefs.min_salary] == [("PLN", "month")]


def test_criterion_defaults_by_type():
    cfg = ScoringConfig(criteria=[
        {"id": "title", "type": "title_match", "weight": 50},
        {"id": "domain", "type": "tokens", "weight": 50, "tokens": ["logistic*"]},
    ])  # fmt: skip
    assert [c.threshold for c in cfg.criteria] == [2, 1]
    validate_scoring(cfg)


def test_config_digest_is_stable_and_sensitive():
    a, b = load_config(None), load_config(None)
    assert config_digest(a) == config_digest(b)
    assert re.fullmatch(r"[0-9a-f]{64}", config_digest(a))
    changed = a.model_copy(update={"demo_today": dt.date(2026, 10, 10)})
    assert config_digest(changed) != config_digest(a)


# ------------------------------------------------------------------ workspace


def test_atomic_write_writes_text_and_bytes(tmp_path):
    target = tmp_path / "deep" / "out" / "report.md"
    workspace.atomic_write(target, "Zażółć\n")
    assert target.read_bytes() == "Zażółć\n".encode("utf-8")
    workspace.atomic_write(target, b"\xef\xbb\xbfbytes")
    assert target.read_bytes() == b"\xef\xbb\xbfbytes"
    assert [p.name for p in target.parent.iterdir()] == ["report.md"]


def test_atomic_write_retries_permission_errors(tmp_path, monkeypatch):
    target = tmp_path / "report.md"
    target.write_text("old", encoding="utf-8")
    real_replace = os.replace
    calls = {"n": 0}
    sleeps = []

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError("held by another program")
        return real_replace(src, dst)

    monkeypatch.setattr(workspace.os, "replace", flaky)
    monkeypatch.setattr(workspace.time, "sleep", sleeps.append)
    workspace.atomic_write(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert calls["n"] == 4 and len(sleeps) == 3


def test_atomic_write_gives_up_after_five_retries_and_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / "report.md"
    target.write_text("old", encoding="utf-8")
    calls = {"n": 0}
    sleeps = []

    def always(src, dst):
        calls["n"] += 1
        raise PermissionError("held")

    monkeypatch.setattr(workspace.os, "replace", always)
    monkeypatch.setattr(workspace.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError):
        workspace.atomic_write(target, "new")
    assert calls["n"] == 6 and sum(sleeps) == pytest.approx(1.0)
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["report.md"]


def test_init_workspace_creates_layout_and_is_idempotent(tmp_path):
    root = tmp_path / "hc"
    created = workspace.init_workspace(root)
    ws = workspace.Workspace.for_root(root)
    for path in (ws.config, ws.ledger, ws.cvs, ws.inbox, ws.evidence, ws.out, root / ".gitignore"):
        assert path.exists() and path in created
    assert (root / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert load_config(ws.config).scoring == Config().scoring
    ledger = Ledger.model_validate(load_yaml(ws.ledger))
    assert ledger.claims == []
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert workspace.init_workspace(root) == []
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_init_workspace_force_backs_up_config_and_keeps_ledger_and_cvs(tmp_path):
    root = tmp_path / "hc"
    workspace.init_workspace(root)
    ws = workspace.Workspace.for_root(root)
    ws.config.write_text("profile:\n  level: mid\n", encoding="utf-8")
    ws.ledger.write_text("schema_version: 1\nperson: {}\nclaims: []\n# mine\n", encoding="utf-8")
    (ws.cvs / "cv.md").write_text("<!-- x -->\nmy cv\n", encoding="utf-8")
    workspace.init_workspace(root, force=True)
    assert (root / "honestcv.yml.bak").read_text(encoding="utf-8") == "profile:\n  level: mid\n"
    assert ws.config.read_text(encoding="utf-8").startswith("# honestcv.yml: every key is optional.")
    assert ws.ledger.read_text(encoding="utf-8").endswith("# mine\n")
    assert (ws.cvs / "cv.md").read_text(encoding="utf-8") == "<!-- x -->\nmy cv\n"


def test_init_workspace_copies_the_cv_once(tmp_path):
    cv = tmp_path / "CV Maya Lindqvist.md"
    cv.write_text("cv text\n", encoding="utf-8")
    root = tmp_path / "hc"
    created = workspace.init_workspace(root, cv=cv)
    assert root / "cvs" / "CV Maya Lindqvist.md" in created
    assert workspace.init_workspace(root, cv=cv) == []
    other = tmp_path / "other" / "CV Maya Lindqvist.md"
    other.parent.mkdir()
    other.write_text("different\n", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        workspace.init_workspace(root, cv=other)
    with pytest.raises(WorkspaceError):
        workspace.init_workspace(root, cv=tmp_path / "missing.pdf")


def test_init_workspace_default_root_is_under_home(_isolated_home):
    workspace.init_workspace()
    assert (Path.home() / "honestcv" / "honestcv.yml").is_file()
    assert workspace.default_root() == Path.home() / "honestcv"


def test_init_writes_semicolon_delimiter_for_comma_locales(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "locale_csv_delimiter", lambda: ";")
    root = tmp_path / "hc"
    workspace.init_workspace(root)
    assert load_config(root / "honestcv.yml").output.csv_delimiter == ";"
    assert workspace.locale_csv_delimiter() in (",", ";")


def test_find_workspace_explicit_parent_and_default(tmp_path, _isolated_home):
    root = tmp_path / "hc"
    workspace.init_workspace(root)
    deep = root / "out" / "runs"
    deep.mkdir(parents=True)
    assert workspace.find_workspace(start=deep).root == root.resolve()
    assert workspace.find_workspace(explicit=root).root == root.resolve()
    with pytest.raises(WorkspaceError):
        workspace.find_workspace(explicit=tmp_path / "nope")
    with pytest.raises(WorkspaceError):
        workspace.find_workspace(explicit=tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(WorkspaceError, match="No workspace here"):
        workspace.find_workspace(start=elsewhere)
    workspace.init_workspace()
    assert workspace.find_workspace(start=elsewhere).root == (Path.home() / "honestcv").resolve()


def test_workspace_paths(tmp_path):
    ws = workspace.Workspace.for_root(tmp_path)
    assert ws.inbox == tmp_path.resolve() / "offers" / "inbox"
    assert ws.state_dir.name == ".honestcv" and ws.config.name == "honestcv.yml" and ws.ledger.name == "ledger.yml"


def test_run_lock_is_exclusive_and_released(workspace):
    lock = workspace.state_dir / "lock"
    with _lock(workspace):
        assert lock.exists()
        with pytest.raises(WorkspaceError, match="Another honestcv run"):
            with _lock(workspace):
                pass
    assert not lock.exists()


def test_run_lock_replaces_a_stale_lock(workspace):
    lock = workspace.state_dir / "lock"
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    lock.write_text("123 0\n", encoding="utf-8")
    old = time.time() - 2 * 3600
    os.utime(lock, (old, old))
    with _lock(workspace):
        assert lock.exists()
    assert not lock.exists()


def _lock(ws):
    from honestcv.workspace import run_lock

    return run_lock(ws)


# ------------------------------------------------------------------ stub modules


@pytest.mark.parametrize(
    "module",
    [
        "honestcv.cli", "honestcv.__main__", "honestcv.pipeline", "honestcv.demo", "honestcv.profile",
        "honestcv.scoring", "honestcv.explain", "honestcv.ledger", "honestcv.checker", "honestcv.tailor",
        "honestcv.state", "honestcv.doctor", "honestcv.schedule", "honestcv.sources", "honestcv.sources.base",
        "honestcv.sources.http", "honestcv.sources.himalayas", "honestcv.sources.remoteok",
        "honestcv.sources.weworkremotely", "honestcv.sources.arbeitnow", "honestcv.sources.ats",
        "honestcv.importers", "honestcv.importers.files", "honestcv.importers.eml", "honestcv.importers.jsonld",
        "honestcv.importers.urls", "honestcv.report", "honestcv.report.markdown", "honestcv.report.html",
        "honestcv.report.matches",
    ],
)  # fmt: skip
def test_every_module_imports(module):
    importlib.import_module(module)


def test_cli_app_is_a_typer_app():
    import typer

    from honestcv.cli import app

    assert isinstance(app, typer.Typer)


# ------------------------------------------------------------------ fictional-sample header


def _header_ok(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    first = text.splitlines()[0] if text else ""
    suffix = path.suffix.lower()
    if suffix in (".yml", ".yaml", ".txt"):
        return first == f"# {HEADER}"
    if suffix in (".md", ".html", ".htm"):
        return first == f"<!-- {HEADER} -->"
    if suffix == ".json":
        data = json.loads(text)
        return isinstance(data, dict) and data.get("_note") == HEADER
    if suffix == ".eml":
        body = text.replace("\r\n", "\n").split("\n\n", 1)
        return len(body) == 2 and body[1].lstrip("\n").splitlines()[0].strip() == HEADER
    if suffix in (".rss", ".xml"):
        return f"<!-- {HEADER} -->" in "\n".join(text.splitlines()[:3])
    return False


def _covered_files():
    files = [p for p in (REPO / "src" / "honestcv" / "samples").rglob("*") if p.is_file()]
    exempt = {GOLDEN / "scan_run_sample.json", GOLDEN / "demo_snapshot.json"}
    for p in (REPO / "tests").rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts or p in exempt:
            continue
        if p.suffix.lower() in (".yml", ".yaml", ".md", ".txt", ".eml", ".rss", ".xml"):
            files.append(p)
    return sorted(files)


def test_fictional_sample_header_in_every_covered_file():
    files = _covered_files()
    assert len(files) >= 25
    missing = [str(p.relative_to(REPO)) for p in files if not _header_ok(p)]
    assert missing == []


# ------------------------------------------------------------------ samples


def _offer_from_import_row(row: dict) -> Offer:
    salary = None
    if "salary_min" in row or "salary_max" in row:
        salary = Salary(min=row.get("salary_min"), max=row.get("salary_max"), currency=row.get("currency"), unit=row.get("salary_unit"))
    keys = ("title", "company", "url", "location", "countries", "remote", "contract", "level", "skills", "skills_nice",
            "languages", "description", "posted_at", "valid_through", "requisition_id")  # fmt: skip
    return Offer(id="0" * 10, source="import:json", attribution="your file sample-offers.json", salary=salary,
                 **{k: row[k] for k in keys if k in row})


def _sample_offers() -> dict[str, Offer]:
    data = json.loads((SAMPLES / "offers" / "inbox" / "sample-offers.json").read_text(encoding="utf-8"))
    assert set(data) == {"_note", "offers"}
    return {row["requisition_id"]: _offer_from_import_row(row) for row in data["offers"]}


def test_sample_config_values():
    cfg = load_config(SAMPLES / "honestcv.yml")
    assert cfg.demo_today == dt.date(2026, 10, 10)
    assert cfg.profile.level == "mid" and cfg.profile.years == 6
    assert cfg.profile.target_titles == ["data analyst", "reporting analyst", "support operations analyst"]
    p = cfg.preferences
    assert (p.remote, p.countries, p.contract, p.deny_titles) == (["remote", "hybrid"], ["SE"], [], ["platform engineer"])
    assert [(m.amount, m.currency, m.unit) for m in p.min_salary] == [(42000, "SEK", "month")]
    assert cfg.scoring == Config().scoring
    assert "years in analytics work; CV dates alone give 10" in (SAMPLES / "honestcv.yml").read_text(encoding="utf-8")


def test_sample_offers_carry_the_frozen_facts():
    offers = _sample_offers()
    assert len(offers) == 8
    for o in offers.values():
        assert dt.date(2026, 9, 20) <= o.posted_at <= dt.date(2026, 10, 8)
        assert o.url.split("/")[2].endswith(".invalid")
    for key, o in offers.items():
        if key == "harrowmere-revenue-reporting-analyst":
            assert o.valid_through == dt.date(2026, 10, 1)
        else:
            assert o.valid_through is None or o.valid_through > dt.date(2026, 10, 10)
    tg = offers["tallgrove-data-analyst"]
    assert (tg.title, tg.company, tg.remote, tg.countries, tg.contract, tg.level) == (
        "Data Analyst", "Tallgrove Analytics", "remote", ["SE"], [], None)
    assert (tg.salary.min, tg.salary.max, tg.salary.currency, tg.salary.unit) == (40000, 48000, "SEK", "month")
    assert tg.skills == ["sql", "python", "looker studio", "excel", "airflow", "dbt"]
    assert tg.skills_nice == ["pandas", "tableau"]
    assert tg.languages == ["en"]
    text = tg.description
    assert "\nRequirements\n- 3+ years of experience\n" in text
    assert "\n\nFluent English required." in text
    required = text.index("required")
    for skill in ("Airflow", "dbt"):
        assert abs(text.index(skill) - required) > 120
    titles = {k: (o.title, o.company) for k, o in offers.items()}
    assert titles == {
        "tallgrove-data-analyst": ("Data Analyst", "Tallgrove Analytics"),
        "quarrylane-analytics-engineer": ("Analytics Engineer", "Quarry Lane Software"),
        "mossby-support-operations-analyst": ("Support Operations Analyst", "Mossby Support Services"),
        "examplebank-junior-reporting-analyst": ("Junior Reporting Analyst", "Example Bank"),
        "tinwhistle-sales-representative": ("Sales Representative", "Tinwhistle Leads"),
        "examplecloud-platform-engineer": ("Platform Engineer", "Example Cloud"),
        "vossgrind-machine-learning-engineer": ("Machine Learning Engineer", "Vossgrind Labs"),
        "harrowmere-revenue-reporting-analyst": ("Revenue Reporting Analyst", "Harrowmere Hotels"),
    }
    assert "5+ years" in offers["quarrylane-analytics-engineer"].description
    assert "2+ years" in offers["mossby-support-operations-analyst"].description
    assert "commission-based only" in offers["tinwhistle-sales-representative"].description
    ceiling = load_yaml(SAMPLES / "ledger.yml")["claims"][3]["blocks_requirements"]
    for key, o in offers.items():
        hits = any(re.search(rx, o.description) for rx in ceiling)
        assert hits is (key == "vossgrind-machine-learning-engineer"), key


def test_sample_cvs():
    analytics = (SAMPLES / "cvs" / "maya-analytics.md").read_text(encoding="utf-8")
    support = (SAMPLES / "cvs" / "maya-support-ops.md").read_text(encoding="utf-8")
    skills_line = next(line for line in analytics.splitlines() if line.startswith("Skills:"))
    for skill in ("sql", "python", "looker studio", "excel", "pandas", "scikit-learn", "google sheets", "zendesk"):
        assert skill in skills_line.lower()
    for absent in ("airflow", "dbt", "tableau", "power bi"):
        assert absent not in analytics.lower()
    for absent in ("python", "pandas", "looker", "tableau", "airflow", "dbt"):
        assert absent not in support.lower()
    assert "power bi" in support.lower()
    for cv in (analytics, support):
        assert "English (C1), Swedish (native)" in cv
        assert "2016-2020" in cv and "2020-2026" in cv
        assert "Brennvik Parcel" in cv


def test_sample_ledger_loads_and_every_evidence_path_exists():
    ledger = Ledger.model_validate(load_yaml(SAMPLES / "ledger.yml"))
    assert [c.id for c in ledger.claims] == ["backlog-dashboard", "tag-cleanup", "churn-capstone", "ml-production-ceiling", "support-career"]
    refs = [e.ref for c in ledger.claims for evs in c.confirmed.values() for e in evs]
    assert len(refs) == 8
    for ref in refs:
        assert (SAMPLES / ref).is_file(), ref


def test_planted_letter_layout():
    lines = (SAMPLES / "letters" / "cover-letter-planted.md").read_text(encoding="utf-8").split("\n")
    assert lines[0] == f"<!-- {HEADER} -->"
    assert [lines[i - 1] for i in (2, 4, 6, 8, 10, 12)] == [""] * 6
    assert lines[12] == "Kind regards," and lines[13] == "Maya Lindqvist"
    assert lines[2] == "I deployed our churn model to production and it cut churn by 12%."
    assert lines[10] == "Built the weekly ticket-backlog dashboard that three support teams use in their Monday reviews."
    assert lines[14:] in ([], [""])


# ------------------------------------------------------------------ golden fixtures


@pytest.mark.parametrize("name", ["ledger_pl.yml", "ledger_rules.yml", "ledger_broken.yml"])
def test_golden_ledgers_load_and_path_refs_exist(name):
    ledger = Ledger.model_validate(load_yaml(GOLDEN / name))
    for claim in ledger.claims:
        for evidences in claim.confirmed.values():
            for e in evidences:
                if not e.ref.startswith(("http://", "https://")):
                    assert (GOLDEN / e.ref).is_file(), e.ref


def test_golden_ledger_contents():
    pl = Ledger.model_validate(load_yaml(GOLDEN / "ledger_pl.yml"))
    assert [c.id for c in pl.claims] == ["raport-rekrutacji", "kurs-power-bi", "brak-zarzadzania", "asystentka-hr"]
    ceiling = pl.claims[2]
    for text in ("Kierowałam zespołem rekrutacji.", "Nie kierowałam jeszcze zespołem.", "Prowadziłam zespół HR."):
        assert any(re.search(rx, text) for rx in ceiling.forbidden_here), text
    for text in ("Prowadziłam rekrutację do zespołu sprzedaży.", *ceiling.allowed):
        assert not any(re.search(rx, text) for rx in ceiling.forbidden_here), text
    rules = Ledger.model_validate(load_yaml(GOLDEN / "ledger_rules.yml"))
    by_id = {c.id: c for c in rules.claims}
    assert by_id["product-descriptions"].visibility == "never" and list(by_id["product-descriptions"].confirmed) == ["generated"]
    assert by_id["feed-integration"].authorship.role == "directed"
    assert by_id["photo-schedule"].draft and by_id["photo-schedule"].allowed == [by_id["photo-schedule"].summary]
    assert by_id["catalogue-years"].review_by < dt.date(2026, 10, 10)
    for ledger in (pl, rules):
        for c in ledger.claims:
            if not c.draft and c.visibility != "never":
                assert c.allowed, c.id
                for q in c.required_qualifiers:
                    assert any(q in sentence for sentence in c.allowed), c.id
    broken = Ledger.model_validate(load_yaml(GOLDEN / "ledger_broken.yml"))
    with pytest.raises(re.error):
        re.compile(broken.claims[1].forbidden_here[0])
    assert broken.claims[0].confirmed["shipped"][0].type == "file"


def test_scan_run_sample_validates_and_covers_every_state():
    run = ScanRun.model_validate_json((GOLDEN / "scan_run_sample.json").read_text(encoding="utf-8"))
    assert run.status == "PARTIAL"
    assert any(s.state == "drift" for s in run.sources)
    assert any(r.low_confidence for r in run.results)
    assert any(r.gate is not None for r in run.results)
    assert any(r.surfaced and any(not c.applicable for c in r.criteria) for r in run.results)
    injection = [o for o in run.offers if o.title == "<img src=https://tracker.invalid/p>"]
    assert len(injection) == 1 and injection[0].url == "javascript:alert(1)"
    assert any(r.surfaced and r.offer_id == injection[0].id for r in run.results)
    polish = [o for o in run.offers if "," in o.title and "ó" in o.title]
    assert polish and any(r.surfaced and r.offer_id == polish[0].id for r in run.results)
    ids = {o.id for o in run.offers}
    assert {r.offer_id for r in run.results} == ids and set(run.new_offer_ids) <= ids
    assert run.config_digest == config_digest(load_config(SAMPLES / "honestcv.yml"))
    dumped = json.loads(run.model_dump_json(by_alias=True))
    assert dumped["schema"] == "honestcv.scan.v1"


def test_scoring_expect_is_consistent():
    data = load_yaml(GOLDEN / "scoring_expect.yml")
    offers = _sample_offers()
    for entry in data["offers_expect"]:
        assert entry["offer"] in offers
    assert data["worked_example"]["offer"] in offers
    for key in data["ranking"]["surfaced"]:
        assert key in offers
    index = LedgerIndex.model_validate(data["ledger_index"])
    ledger = Ledger.model_validate(load_yaml(SAMPLES / "ledger.yml"))
    expected_backing: dict[str, list[str]] = {}
    for c in ledger.claims:
        if not c.draft and c.confirmed:
            for skill in c.skills:
                expected_backing.setdefault(skill, []).append(c.id)
    assert index.skill_backing == expected_backing
    assert index.ceiling_patterns == [("ml-production-ceiling", rx) for rx in ledger.claims[3].blocks_requirements]
    we = data["worked_example"]
    weights = {c["id"]: c["weight"] for c in we["criteria"]}
    assert sum(weights.values()) == 100
    wa = sum(c["weight"] for c in we["criteria"] if c["applicable"])
    assert wa == we["applicable_weight"] == 95
    total = 0.0
    for c in we["criteria"]:
        if c["applicable"]:
            points = 100 * c["weight"] * c["fraction"] / wa
            assert points == pytest.approx(c["points"], abs=we["points_tolerance"])
            total += 100 * c["weight"] * (4.5 / 7 if c["id"] == "skills" else c["fraction"]) / wa
    assert total == pytest.approx(we["base"], abs=we["base_tolerance"])
    assert round(total) == we["match_pct"] == 83
    reqs = we["requirements"]
    matched = [r for r in reqs if r["in_profile"]]
    assert (sum(1 for r in matched if r["backed_by"]), len(matched)) == tuple(we["backed"]) == (4, 5)
    assert len(reqs) == we["requirements_recognized"] == 8
    tg = offers["tallgrove-data-analyst"]
    assert sorted(r["term"] for r in reqs) == sorted(tg.skills + tg.skills_nice)


def _checker_cases():
    bad = load_yaml(GOLDEN / "checker_bad.yml")
    good = load_yaml(GOLDEN / "checker_good.yml")
    return bad, good


def test_checker_golden_files_are_well_formed():
    bad, good = _checker_cases()
    rule_codes = TypeAdapter(RuleCode)
    severities = TypeAdapter(Severity)
    ledgers = set(bad["ws_root"])
    for ledger, root in bad["ws_root"].items():
        assert (REPO / ledger).is_file() and (REPO / root).is_dir()
    counts: dict[str, int] = {}
    planted = bad["planted_letter"]
    letter = (REPO / planted["document"]).read_text(encoding="utf-8").split("\n")
    for line_no, case in planted["lines"].items():
        assert letter[line_no - 1] == case["text"]
        for flag in case["expect"]:
            rule_codes.validate_python(flag["rule"])
            severities.validate_python(flag["severity"])
            counts[flag["rule"]] = counts.get(flag["rule"], 0) + 1
    ids = set()
    for case in bad["lines"]:
        assert case["id"] not in ids
        ids.add(case["id"])
        assert case["ledger"] in ledgers
        assert case["expect"], case["id"]
        for rule in {flag["rule"] for flag in case["expect"]}:
            counts[rule] = counts.get(rule, 0) + 1
        for flag in case["expect"]:
            rule_codes.validate_python(flag["rule"])
            severities.validate_python(flag["severity"])
    all_codes = set(RuleCode.__args__)
    assert {code for code, n in counts.items() if n >= 2} == all_codes
    assert len(good["lines"]) >= 12
    for case in good["lines"]:
        assert case["ledger"] in ledgers and case["text"]
    good_texts = {(c["ledger"], c["text"]) for c in good["lines"]}
    for ledger_path in ("src/honestcv/samples/en/ledger.yml", "tests/golden/ledger_pl.yml"):
        ledger = Ledger.model_validate(load_yaml(REPO / ledger_path))
        for claim in ledger.claims:
            if not claim.draft:
                for sentence in claim.allowed:
                    assert (ledger_path, sentence) in good_texts, sentence


# ---------------------------------------------------------------- preferences.countries

COUNTRY_NAMES = {
    "PL": ["Polska", "Poland"], "SE": ["Sverige", "Sweden"], "DE": ["Deutschland", "Germany", "Niemcy"],
    "GB": ["United Kingdom", "UK", "Great Britain"], "US": ["United States", "USA", "US"],
    "NL": ["Netherlands", "Nederland", "Holandia"], "ES": ["Spain", "España", "Hiszpania"], "FR": ["France", "Francja"],
    "CZ": ["Czechia", "Czech Republic", "Czechy"], "IE": ["Ireland", "Irlandia"], "NO": ["Norway", "Norge", "Norwegia"],
    "DK": ["Denmark", "Danmark", "Dania"], "FI": ["Finland", "Suomi", "Finlandia"], "AT": ["Austria"],
    "CH": ["Switzerland", "Szwajcaria"], "PT": ["Portugal", "Portugalia"], "IT": ["Italy", "Włochy"],
}  # fmt: skip


def test_country_names_map_to_two_letter_codes():
    for code, names in COUNTRY_NAMES.items():
        for name in names:
            assert Preferences(countries=[name]).countries == [code], name
            assert Preferences(countries=[f"  {name.upper()} "]).countries == [code], name  # case and spaces do not matter
    assert Preferences(countries=["Espana", "wlochy", "SVERIGE", "se", "uk", "Pl"]).countries == ["ES", "IT", "SE", "SE", "GB", "PL"]
    assert Preferences(countries=["Poland", "SE", "Niemcy"]).countries == ["PL", "SE", "DE"]  # names and codes mix
    assert Preferences().countries == []


@pytest.mark.parametrize("value", ["Atlantis", "POL", "P", "", "  ", "P1", "ąę", "Sweden!", "SE,PL"])
def test_a_country_that_is_not_a_code_or_a_known_name_is_refused(value):
    with pytest.raises(ValidationError) as err:
        Preferences(countries=["PL", value])
    assert "Use two-letter country codes, for example PL or SE" in str(err.value)
    assert [e["loc"] for e in err.value.errors()] == [("countries",)]


def test_a_bad_country_in_honestcv_yml_is_reported_with_its_line(tmp_path):
    path = tmp_path / "honestcv.yml"
    path.write_text("preferences:\n  remote: [remote]\n  countries: [Atlantis]\n", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_config(path)
    assert err.value.message.startswith("'countries' is not valid (use two-letter country codes") and "countries: [PL, SE]" in err.value.message
    assert err.value.line == 3 and "Atlantis" not in err.value.message
    path.write_text("preferences:\n  countries: [Polska, Sweden, de]\n", encoding="utf-8")
    assert load_config(path).preferences.countries == ["PL", "SE", "DE"]


def test_the_country_hint_keeps_the_codes_in_upper_case(tmp_path):
    path = tmp_path / "honestcv.yml"
    path.write_text("preferences:\n  countries: [Atlantis]\n", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_config(path)
    assert err.value.message == "'countries' is not valid (use two-letter country codes, for example PL or SE), for example: countries: [PL, SE]"
