"""CV reading and profile extraction."""

import datetime as dt
import io
import json
import random
import shutil
import time
import zipfile
from pathlib import Path

import pytest
import yaml
from pypdf import PdfReader, PdfWriter

from honestcv import profile as prof
from honestcv.config import Config, ProfileOverrides, load_config
from honestcv.errors import WorkspaceError
from honestcv.profile import canonical, extract_profile, load_cv_text, load_profiles, pack
from honestcv.textutil import _token_regex, hit

TODAY = dt.date(2026, 10, 10)
NO_OVERRIDES = ProfileOverrides()


def make_pdf(lines: list[str]) -> bytes:
    """A one-page text PDF (Helvetica, ASCII only) with a correct cross-reference table."""
    content = ("BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET").encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(body: str) -> bytes:
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" xmlns:v="urn:schemas-microsoft-com:vml">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("word/document.xml", doc)
    return buf.getvalue()


def para(text: str, style: str = "", bullet: bool = False) -> str:
    props = (f'<w:pStyle w:val="{style}"/>' if style else "") + ("<w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"1\"/></w:numPr>" if bullet else "")
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"


def test_cv_formats(tmp_path):
    md = tmp_path / "cv.md"
    md.write_text("# Anna Kowalska\n\nSkills: SQL, Python\n", encoding="utf-8")
    txt = tmp_path / "cv.txt"
    txt.write_text("Anna Kowalska\nSkills: SQL, Python\n", encoding="utf-8")
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(make_pdf(["Anna Kowalska", "Skills: SQL, Python"]))
    box = (
        "<w:p><w:r><mc:AlternateContent><mc:Choice Requires=\"wps\"><w:drawing><wps:txbx><w:txbxContent>"
        "<w:p><w:r><w:t>Box text</w:t></w:r></w:p></w:txbxContent></wps:txbx></w:drawing></mc:Choice>"
        "<mc:Fallback><w:pict><v:textbox><w:txbxContent><w:p><w:r><w:t>Box text</w:t></w:r></w:p></w:txbxContent>"
        "</v:textbox></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    docx = tmp_path / "cv.docx"
    docx.write_bytes(make_docx(para("Experience", "Heading1") + para("Skills: SQL, Python") + para("Built reports", bullet=True) + box))

    assert "Skills: SQL, Python" in load_cv_text(md).text
    assert load_cv_text(txt).text.startswith("Anna Kowalska")
    loaded = load_cv_text(pdf)
    assert loaded.pages == 1 and "Skills: SQL, Python" in loaded.text
    text = load_cv_text(docx).text
    assert text.count("Box text") == 1  # mc:Fallback is skipped, so the text box is not read twice
    assert "## Experience" in text and "- Built reports" in text

    for name in ("cv.doc", "cv.odt", "cv.pages", "cv.rtf", "cv.webarchive"):
        path = tmp_path / name
        path.write_bytes(b"x")
        with pytest.raises(WorkspaceError) as err:
            load_cv_text(path)
        assert err.value.hint == "Save it as PDF or DOCX, then try again."
    with pytest.raises(WorkspaceError) as err:
        load_cv_text(tmp_path / "missing.md")
    assert "not found" in err.value.message

    locked = PdfWriter(clone_from=PdfReader(str(pdf)))
    locked.encrypt("secret", algorithm="RC4-128")
    protected = tmp_path / "locked.pdf"
    with protected.open("wb") as handle:
        locked.write(handle)
    with pytest.raises(WorkspaceError) as err:
        load_cv_text(protected)
    assert err.value.message == "This PDF is protected."
    assert err.value.hint == "Export it again without protection, or as DOCX."

    blank = tmp_path / "scan.pdf"
    blank.write_bytes(make_pdf([]))
    with pytest.raises(WorkspaceError) as err:
        load_cv_text(blank)
    assert err.value.message == "This PDF has no text layer (it is an image)."
    assert err.value.hint == "Export your CV as text-based PDF or DOCX."

    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(WorkspaceError):
        load_cv_text(broken)
    assert load_cv_text(pdf).warnings == []


def test_text_normalize(tmp_path, monkeypatch):
    cv = tmp_path / "cv.md"
    cv.write_text(
        "Skills: \ufb01nancial reporting, SQL, Pytho\u00adn\n"  # fi ligature, soft hyphen
        "Led the ana-\nlysis of \u201cdaily\u201d reports, it\u2019s done.\n"  # line-end hyphen, typographic quotes
        "Zesp\u00f3\u0142 a\u0328 \ufb02ow\n",  # decomposed Polish letter (a + ogonek), fl ligature
        encoding="utf-8",
    )
    text = load_cv_text(cv).text
    assert "financial reporting" in text and "Python" in text
    assert "analysis" in text
    assert '"daily"' in text and "it's done" in text
    assert "\u0105" in text and "a\u0328" not in text  # composed into one letter
    assert "flow" in text and "\ufb02" not in text

    # a text file that is not UTF-8 is read as Windows-1250 with a warning
    legacy = tmp_path / "old.txt"
    legacy.write_bytes("Zespół ściąga".encode("cp1250"))
    loaded = load_cv_text(legacy)
    assert "Zespół" in loaded.text and loaded.warnings and "Windows-1250" in loaded.warnings[0]

    # a PDF whose text is more than 1% U+FFFD or stray combining marks gets the Polish-letters warning
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(make_pdf(["x"]))
    for garbage in ("Praca w firmie " + "\ufffd" * 12 + " koniec tekstu", "Prac" + "x\u0301" * 10 + " zwykly tekst jest tutaj"):
        monkeypatch.setattr(prof, "_read_pdf", lambda path, t=garbage: (t, 1))
        assert prof.BAD_LETTERS in load_cv_text(pdf).warnings
    monkeypatch.setattr(prof, "_read_pdf", lambda path: ("Zwykly tekst bez problemow z literami " * 5, 1))
    assert load_cv_text(pdf).warnings == []


POLISH_CV = """\
# Anna Kowalska

## Doświadczenie zawodowe

### Asystentka HR, ABC Sp. z o.o., od 2019 do obecnie

- Prowadziłam rekrutację i obsługę kadr.

### Recepcjonistka | XYZ | 03/2015 - 2018

- Obsługa klienta w recepcji.

## Wykształcenie

### Zarządzanie, Uniwersytet Przykładowy, 2010-2015

## Umiejętności

Excel, Power BI, SQL

## Języki

Polski (ojczysty), angielski (B2)
"""


def test_extract_profile(tmp_path):
    cv = tmp_path / "anna.md"
    cv.write_text(POLISH_CV, encoding="utf-8")
    p = extract_profile(cv, NO_OVERRIDES, today=TODAY)
    assert p.cv_id == "anna.md" and len(p.cv_sha256) == 64
    assert p.titles == ["Asystentka HR", "Recepcjonistka"]
    assert p.experience == [("Asystentka HR", "2019-present"), ("Recepcjonistka", "2015-2018")]
    assert p.years == 10.0  # 2019 to now (7) + 2015-2018 (3); the 2010-2015 degree is education and not counted
    assert p.level is None  # never guessed
    assert {"excel", "power bi", "sql", "customer support"} <= set(p.skills)  # Polish alias "obsługa klienta"
    assert p.languages == {"en": "B2", "pl": "native"}
    assert p.target_titles == p.titles

    overrides = ProfileOverrides(level="mid", years=6, target_titles=["recruiter"], skills_add=["K8s", "Dbt"], skills_remove=["SQL"])
    q = extract_profile(cv, overrides, today=TODAY)
    assert q.level == "mid" and q.years == 6 and q.target_titles == ["recruiter"]
    assert "kubernetes" in q.skills and "dbt" in q.skills and "sql" not in q.skills
    assert q.titles == p.titles  # titles still come from the CV


@pytest.mark.parametrize(
    ("ranges", "years"),
    [
        ("2015-2020\n2018-2022", 7.0),  # overlap merged
        ("2010-2012\n2015-2016", 3.0),  # gap not counted
        ("2016-2020\n2020-2026", 10.0),  # touching ranges
        ("from 2021 to 2026", 5.0),
        ("od 2024 do obecnie", 2.0),
        ("2020-present", 6.0),
    ],
)
def test_years_from_date_ranges(tmp_path, ranges, years):
    body = "".join(f"### Analyst {i}, Example Co, {line}\n\n" for i, line in enumerate(ranges.split("\n")))
    cv = tmp_path / "cv.md"
    cv.write_text("# Name\n\n## Experience\n\n" + body + "## Education\n\n### Degree, 1990-2000\n", encoding="utf-8")
    assert extract_profile(cv, NO_OVERRIDES, today=TODAY).years == years


def test_experience_title_on_its_own_line(tmp_path):
    cv = tmp_path / "cv.txt"
    cv.write_text("Name\n\nEXPERIENCE\nSales Coordinator\n2020 - present\nExample Co\nOffice Assistant 2017-2020\n\nEDUCATION\nCollege 1999-2003\n", encoding="utf-8")
    p = extract_profile(cv, NO_OVERRIDES, today=TODAY)
    assert p.experience == [("Sales Coordinator", "2020-present"), ("Office Assistant", "2017-2020")]
    assert p.years == 9.0  # the same 9 years START-HERE.md shows


def test_skills_come_most_prominent_first(tmp_path):
    cv = tmp_path / "cv.md"
    cv.write_text("## Experience\n\n- Built Tableau reports.\n- Cleaned data in Tableau and SQL.\n- Monthly Tableau summary.\n\nSkills: Python, SQL, Tableau, Excel\n", encoding="utf-8")
    # tableau has four mentions, sql two; python and excel (one each, excel only through the skills line) follow in the CV's order
    assert extract_profile(cv, NO_OVERRIDES, today=TODAY).skills == ["tableau", "sql", "python", "excel"]
    added = extract_profile(cv, ProfileOverrides(skills_add=["dbt"], skills_remove=["tableau"]), today=TODAY).skills
    assert added == ["dbt", "sql", "python", "excel"]  # what the person adds comes first


def test_no_dates_gives_no_years(tmp_path):
    cv = tmp_path / "cv.md"
    cv.write_text("# Name\n\nSkills: SQL\n", encoding="utf-8")
    p = extract_profile(cv, NO_OVERRIDES, today=TODAY)
    assert p.years is None and p.titles == [] and p.experience == []


def test_languages_only_with_level_or_section(tmp_path):
    cv = tmp_path / "cv.md"
    cv.write_text("## Experience\n\n- Translated manuals from German into English.\n- Fluent Swedish at the front desk.\n\n## Languages\n\nPolish\n", encoding="utf-8")
    assert extract_profile(cv, NO_OVERRIDES, today=TODAY).languages == {"pl": "listed", "sv": "fluent"}


def test_ambiguous_skills_on_a_skills_line_only(tmp_path):
    cv = tmp_path / "cv.md"
    cv.write_text("## Summary\n\nI like to go to the rest of the sites and make things.\n\nSkills: Go, R, Make, Excel\n", encoding="utf-8")
    skills = extract_profile(cv, NO_OVERRIDES, today=TODAY).skills
    assert {"go", "r", "make", "excel"} <= set(skills)
    free = tmp_path / "free.md"
    free.write_text("I like to go to the rest of the sites, make things and excel at it.\n", encoding="utf-8")
    assert extract_profile(free, NO_OVERRIDES, today=TODAY).skills == []


def test_sample_cvs(samples_dir):
    cfg = load_config(samples_dir / "honestcv.yml")
    a = extract_profile(samples_dir / "cvs" / "maya-analytics.md", cfg.profile, today=TODAY)
    assert {"sql", "python", "looker studio", "excel", "pandas", "scikit-learn", "google sheets", "zendesk"} <= set(a.skills)
    assert not {"airflow", "dbt", "tableau", "looker"} & set(a.skills)  # "looker studio" is not also "looker"
    assert (a.level, a.years, a.languages) == ("mid", 6, {"en": "C1", "sv": "native"})
    assert extract_profile(samples_dir / "cvs" / "maya-analytics.md", NO_OVERRIDES, today=TODAY).years == 10.0  # CV dates alone
    b = extract_profile(samples_dir / "cvs" / "maya-support-ops.md", cfg.profile, today=TODAY)
    assert {"zendesk", "customer support", "sql", "excel", "google sheets", "power bi"} <= set(b.skills)


def test_load_profiles_cache_and_overrides(sample_workspace, monkeypatch):
    ws = sample_workspace
    cfg = load_config(ws.config)
    profiles = load_profiles(ws, cfg, today=TODAY)
    assert [p.cv_id for p in profiles] == ["maya-analytics.md", "maya-support-ops.md"]
    assert len(list((ws.state_dir / "profiles").glob("*.json"))) == 2

    def boom(path):
        raise AssertionError("the cache should have been used")

    monkeypatch.setattr(prof, "_analyze", boom)
    again = load_profiles(ws, cfg, today=TODAY)
    assert [p.model_dump() for p in again] == [p.model_dump() for p in profiles]
    # overrides are applied on top of the cached file reading
    cfg2 = Config.model_validate({"profile": {"level": "senior", "skills_remove": ["sql"]}})
    p = load_profiles(ws, cfg2, today=TODAY)[0]
    assert p.level == "senior" and "sql" not in p.skills and p.years == 10.0


def test_load_profiles_without_cache_writes_nothing(sample_workspace, tmp_path):
    ws = sample_workspace
    before = sorted(str(p.relative_to(ws.root)) for p in ws.root.rglob("*"))
    assert len(load_profiles(ws, load_config(ws.config), today=TODAY, cache=False)) == 2
    assert sorted(str(p.relative_to(ws.root)) for p in ws.root.rglob("*")) == before


def test_load_profiles_skips_noise_and_refuses_legacy(sample_workspace):
    ws = sample_workspace
    (ws.cvs / ".DS_Store").write_bytes(b"x")
    (ws.cvs / "notes.csv").write_text("a,b", encoding="utf-8")
    assert len(load_profiles(ws, Config(), today=TODAY, cache=False)) == 2
    shutil.copy(ws.cvs / "maya-analytics.md", ws.cvs / "old-cv.doc")
    with pytest.raises(WorkspaceError) as err:
        load_profiles(ws, Config(), today=TODAY, cache=False)
    assert err.value.hint == "Save it as PDF or DOCX, then try again."
    empty = Path(ws.root) / "nothing"
    assert load_profiles(type(ws).for_root(empty), Config(), cache=False) == []


def test_data_packs_are_plain_text_and_complete():
    banned = ("scr" + "ap", "byp" + "ass", "stea" + "lth", "undetect" + "able")

    def leaves(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                assert isinstance(k, str), f"{key}: key {k!r} is not text (quote words like no and yes)"
                yield from leaves(v, k)
        elif isinstance(node, list):
            for v in node:
                yield from leaves(v, key)
        else:
            yield key, node

    for name in ("skills.yml", "levels.yml", "gates.yml", "languages.yml"):
        for key, value in leaves(pack(name)):
            assert (key == "ambiguous" and value is True) or isinstance(value, (str, int)) and not isinstance(value, bool), (name, key, value)
            assert not any(word in str(value).lower() for word in banned)
    names = {e["name"] for e in pack("skills.yml")["skills"]}
    required = {"sql", "python", "looker studio", "excel", "airflow", "dbt", "pandas", "tableau", "scikit-learn", "google sheets", "zendesk",
                "customer support", "kubernetes", "power bi"}  # fmt: skip
    assert required <= names and "analytics" not in names
    every = {t for e in pack("skills.yml")["skills"] for t in (e["name"], *e.get("aliases", []))}
    assert "analytics" not in every
    assert len(names) == len(pack("skills.yml")["skills"])  # no duplicate canonical name
    ambiguous = {e["name"] for e in pack("skills.yml")["skills"] if e.get("ambiguous")}
    assert {"go", "r", "c", "make", "excel", "spark", "swift", "access"} <= ambiguous
    assert canonical("K8s") == "kubernetes" and canonical("Postgres") == "postgresql" and canonical("JS") == "javascript"
    assert canonical("Unknown Tool") == "unknown tool"


# ---------------------------------------------------------------- empty CVs


def test_a_cv_without_text_is_refused_in_every_format(tmp_path):
    empty_md = tmp_path / "empty.md"
    empty_md.write_text("", encoding="utf-8")
    blank_txt = tmp_path / "blank.txt"
    blank_txt.write_text("  \n\t\r\n \n", encoding="utf-8")
    controls_only = tmp_path / "controls.txt"
    controls_only.write_bytes(b"\x1b\x00\x07\n \x7f")
    no_text_docx = tmp_path / "no-text.docx"
    no_text_docx.write_bytes(make_docx(para("") + para("   ")))
    bare_docx = tmp_path / "bare.docx"
    bare_docx.write_bytes(make_docx(""))
    for path in (empty_md, blank_txt, controls_only, no_text_docx, bare_docx):
        with pytest.raises(WorkspaceError) as err:
            load_cv_text(path)
        assert err.value.message == f"{path.name} has no readable text."
        assert err.value.path == path.name and err.value.hint  # names the file and says how to fix it
        assert "docx" in err.value.hint.lower()

    one_word = tmp_path / "one.md"
    one_word.write_text("Python", encoding="utf-8")
    assert load_cv_text(one_word).text == "Python"  # any text at all is readable


def test_an_empty_cv_stops_profile_loading_with_the_same_error(sample_workspace):
    ws = sample_workspace
    (ws.cvs / "empty.md").write_text("\n", encoding="utf-8")
    with pytest.raises(WorkspaceError) as err:
        load_profiles(ws, Config(), today=TODAY, cache=False)
    assert err.value.message == "empty.md has no readable text."


# ---------------------------------------------------------------- scan_skills against the pre-fix reference


def _reference_sentence_start(low: str, pos: int) -> int:
    k = low.rfind(". ", 0, pos)
    return max(low.rfind("\n", 0, pos) + 1, k + 2 if k >= 0 else 0, pos - 40, 0)


def _reference_negated(low: str, s: int, e: int) -> bool:
    cues, after = pack("skills.yml"), e + 40
    for sep in ("\n", ". "):
        k = low.find(sep, e)
        after = min(after, k) if k >= 0 else after
    before_text, after_text = low[_reference_sentence_start(low, s) : s], low[e:after]
    return any(hit(before_text, n) for n in cues["negations_before"]) or any(hit(after_text, n) for n in cues["negations_after"])


def _reference_found(low: str, direct: bool) -> set[tuple[str, int, int]]:
    found = set()
    for name, (free, every) in prof._skills().items():
        for token in dict.fromkeys(every if direct else free):
            found.update((name, m.start(), m.end()) for m in _token_regex(token).finditer(low) if not _reference_negated(low, m.start(), m.end()))
    return found


def _reference_scan_skills(found: set[tuple[str, int, int]]) -> tuple[tuple[str, int, int], ...]:
    """The containment rule as it was before the linear version: every pair of mentions compared."""
    inside = {h for h in found for o in found if o[0] != h[0] and o[1] <= h[1] and h[2] <= o[2] and o[2] - o[1] > h[2] - h[1]}
    return tuple(sorted(found - inside, key=lambda h: (h[1], h[2], h[0])))


def _golden_texts() -> list[str]:
    def leaves(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from leaves(value)
        elif isinstance(node, list):
            for value in node:
                yield from leaves(value)
        elif isinstance(node, str) and len(node) > 20:
            yield node

    golden = Path(__file__).resolve().parents[1] / "golden"
    out: list[str] = []
    for path in sorted(golden.glob("*.yml")):
        out += list(leaves(yaml.safe_load(path.read_text(encoding="utf-8"))))
    for path in sorted(golden.glob("*.json")):
        out += list(leaves(json.loads(path.read_text(encoding="utf-8"))))
    for path in sorted(golden.rglob("*.txt")):
        out.append(path.read_text(encoding="utf-8"))
    return out


def _random_texts(count: int) -> list[str]:
    rng = random.Random(20261011)
    tokens = sorted({t for e in pack("skills.yml")["skills"] for t in (e["name"], *e.get("aliases", []))})
    cues = sorted({*pack("skills.yml")["negations_before"], *pack("skills.yml")["negations_after"]})
    pieces = [*tokens, *cues, "experience with", "we need", "nice to have", "required", "team", "and", "or", "of", "the"]
    glue = [" ", " ", " ", ", ", ". ", ".\n", "\n", "\n- ", "; ", " / "]
    texts = []
    for _ in range(count):
        parts = []
        for _ in range(rng.randint(3, 60)):
            word = rng.choice(pieces)
            parts.append(word[:-1] + rng.choice(["", "3", "ista", "s"]) if word.endswith("*") else word)
            parts.append(rng.choice(glue))
        texts.append("".join(parts))
    return texts


def test_scan_skills_gives_the_same_answer_as_the_all_pairs_reader(samples_dir):
    samples = [p.read_text(encoding="utf-8") for p in sorted(samples_dir.rglob("*")) if p.suffix in (".md", ".txt")]
    offers = json.loads((samples_dir / "offers" / "inbox" / "sample-offers.json").read_text(encoding="utf-8"))["offers"]
    samples += [str(o.get("description", "")) + "\n" + str(o.get("title", "")) for o in offers]
    every_token = [t for e in pack("skills.yml")["skills"] for t in (e["name"], *e.get("aliases", []))]
    inputs = [*samples, *_golden_texts(), *every_token, *(f"We need {t} and {t} experience, not {t}." for t in every_token), *_random_texts(200)]
    assert len(samples) >= 10 and len(inputs) > 600
    dropped = 0
    for text in inputs:
        low = text.lower()
        for direct in (False, True):
            found = _reference_found(low, direct)
            expected = _reference_scan_skills(found)
            dropped += len(found) - len(expected)
            assert prof.scan_skills.__wrapped__(low, direct) == expected, (direct, text[:200])
    assert dropped > 100  # the inputs do contain mentions that sit inside a longer mention of another skill
    # the case the containment rule exists for
    assert [h[0] for h in prof.scan_skills("reports in looker studio and looker")] == ["looker studio", "looker"]


def test_scan_skills_of_a_huge_text_is_fast():
    def best_of_three(text: str) -> tuple[float, tuple]:
        times = []
        for _ in range(3):
            prof.scan_skills.cache_clear()
            start = time.perf_counter()
            result = prof.scan_skills(text)
            times.append(time.perf_counter() - start)
        return min(times), result

    seconds, result = best_of_three("python sql " * 10000)
    assert len(result) == 20000 and seconds < 0.5, seconds
    seconds, result = best_of_three("looker studio " * 5000)  # every mention holds a shorter mention of another skill
    assert [h[0] for h in result] == ["looker studio"] * 5000 and seconds < 2.0, seconds
