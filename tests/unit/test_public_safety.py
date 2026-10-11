"""What a public repository must never contain.

Every pattern is assembled from fragments, so this file never contains the words it looks for.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SLUG = "kuliberdalabs/honestcv-job-match"
OWNER = "kuli" + "berda"
BRAND_FILES = {"README.md", "README.pl.md", "LICENSE", "SECURITY.md", "CHANGELOG.md"}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "dist", "build", "node_modules"}
BINARY = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".docx", ".zip", ".whl", ".gz"}


def tracked_files() -> list[Path]:
    """Files git tracks plus new files it does not ignore; without git, every file outside the usual tool folders."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO, capture_output=True, check=True, timeout=60)
        names = sorted({n for n in out.stdout.decode("utf-8").split("\0") if n})
        files = [REPO / n for n in names]
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        files = [p for p in REPO.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.relative_to(REPO).parts)]
    return [p for p in files if p.is_file() and p.suffix.lower() not in BINARY and p.name != "uv.lock"]


FILES = tracked_files()


def text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def hits(pattern: re.Pattern, files) -> list[str]:
    return [f"{rel(p)}:{n}: {m.group(0)}" for p in files for n, line in enumerate(text_of(p).splitlines(), 1) for m in pattern.finditer(line)]


def test_files_were_found():
    names = {rel(p) for p in FILES}
    assert {"pyproject.toml", "src/honestcv/cli.py", "tests/unit/test_public_safety.py"} <= names


def test_no_email_addresses_except_example_invalid():
    email = re.compile(r"[A-Za-z0-9._%+-]+" + "@" + r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    bad = [h for h in hits(email, FILES) if not h.endswith("@example.invalid") and not (h.startswith("SECURITY.md:") and h.endswith("@" + OWNER + ".ai"))]
    assert bad == []
    security = [h for h in hits(email, [p for p in FILES if rel(p) == "SECURITY.md"]) if not h.endswith("@example.invalid")]
    assert len(security) <= 1


def test_no_phone_numbers():
    phone = re.compile(r"(?<![\w.+-])\+\d{2,3}[ -]?\d{2,3}[ -]?\d{3}[ -]?\d{2,4}(?!\w)|(?<![\w.-])\d{3}[ -]\d{3}[ -]\d{3,4}(?![\w.-])")
    assert hits(phone, FILES) == []


def test_no_home_folder_paths():
    paths = re.compile("/" + "Users/|/" + "home/")
    assert hits(paths, FILES) == []


def test_no_banned_vocabulary():
    words = ["scr" + "ap(e|er|ers|es|ed|ing)", "by" + "pass", "ste" + "alth", "undetect" + "able"]
    banned = re.compile(r"(?i)\b(" + "|".join(words) + r")\b")
    assert hits(banned, FILES) == []


def test_no_bare_name_install_line():
    lines = [r"uv\s+tool\s+install\s+" + "honest" + r"cv\b(?![\w./-])", r"pipx\s+install\s+" + "honest" + "cv", r"uvx\s+" + "honest" + r"cv\b"]
    pattern = re.compile("|".join(lines))
    assert hits(pattern, FILES) == []


def test_no_references_to_earlier_tools():
    phrases = re.compile(r"(?i)\b(" + "|".join(["priv" + "ate tool", "orig" + "inal tool", "priv" + "ate code"]) + r")\b")
    assert hits(phrases, FILES) == []


def test_no_loaded_words_in_docs_and_package_metadata():
    loaded = ["du" + "mp", "le" + "ak", "cl" + "one", "expl" + "oit", "arbit" + "rage", "cr" + "ack", "un" + "lock", "cr" + "awl", "har" + "vest"]
    pattern = re.compile(r"(?i)\b(" + "|".join(loaded) + ")")
    assert hits(pattern, [p for p in FILES if p.suffix.lower() == ".md"]) == []
    meta = text_of(REPO / "pyproject.toml")
    description = re.search(r'^description\s*=\s*"(.*)"', meta, re.M)
    keywords = re.search(r"^keywords\s*=\s*\[(.*)\]", meta, re.M)
    assert description and keywords
    assert pattern.findall(description[1] + " " + keywords[1]) == []


def test_owner_name_only_where_allowed():
    pattern = re.compile(r"(?i)\b" + OWNER + r"\b")
    bad = []
    for path in FILES:
        name = rel(path)
        for n, line in enumerate(text_of(path).splitlines(), 1):
            if not pattern.search(line.replace(SLUG, "")):
                continue
            if name in BRAND_FILES or (name == "pyproject.toml" and line.strip().startswith("authors")):
                continue
            bad.append(f"{name}:{n}")
    assert bad == []


def test_private_denylist():
    listing = os.environ.get("HONESTCV_PRIVATE_DENYLIST")
    if not listing or not Path(listing).is_file():
        pytest.skip("HONESTCV_PRIVATE_DENYLIST not set")
    patterns = [re.compile(line.strip(), re.IGNORECASE) for line in Path(listing).read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    security = text_of(REPO / "SECURITY.md") if (REPO / "SECURITY.md").is_file() else ""
    contact = re.findall(r"[A-Za-z0-9._%+-]+" + "@" + OWNER + r"\.ai", security)
    allowed = ["Kuli" + "berda Labs", OWNER + ".ai", *contact]
    bad = []
    for path in FILES:
        text = text_of(path).replace(SLUG, "")
        if rel(path) in BRAND_FILES | {"pyproject.toml"}:
            for word in sorted(allowed, key=len, reverse=True):
                text = text.replace(word, "")
        bad += [f"{rel(path)}: pattern {i + 1}" for i, rx in enumerate(patterns) if rx.search(text)]
    assert bad == []
