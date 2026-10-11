"""Helpers for the integration tests: an in-process CLI runner and the JSON schemas documented in docs/agents.md."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from honestcv.cli import app

REPO = Path(__file__).resolve().parents[2]
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list, "null": type(None)}


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Run honestcv in this process from an empty folder; returns the click Result (stdout and stderr apart)."""
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)
    runner = CliRunner()

    def invoke(*args, input=None):
        return runner.invoke(app, [str(a) for a in args], input=input)

    return invoke


def run_module(args: list[str], *, cwd: Path, home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """python -m honestcv in a child process with its own home folder; output is returned as bytes."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "HONESTCV"))}
    env.update({"HOME": str(home), "USERPROFILE": str(home), **(extra_env or {})})
    return subprocess.run([sys.executable, "-m", "honestcv", *args], cwd=cwd, env=env, capture_output=True, timeout=180, check=False)


@pytest.fixture(scope="session")
def schemas() -> dict[str, dict]:
    """Every JSON schema block in docs/agents.md, by its $id."""
    text = (REPO / "docs" / "agents.md").read_text(encoding="utf-8")
    found = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)]
    return {s["$id"]: s for s in found}


def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    """A small validator for the keywords docs/agents.md uses: type, const, enum, required, properties, items."""
    errors = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(isinstance(value, _TYPES[k]) and not (k in ("integer", "number") and isinstance(value, bool)) for k in kinds):
            return errors + [f"{path}: expected {kinds}, got {type(value).__name__}"]
    if isinstance(value, dict):
        errors += [f"{path}: missing {key}" for key in schema.get("required", []) if key not in value]
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += schema_errors(value[key], sub, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += schema_errors(item, schema["items"], f"{path}[{i}]")
    return errors


@pytest.fixture
def validate():
    return schema_errors


@pytest.fixture
def run_child():
    return run_module


@pytest.fixture(scope="session")
def demo_run(tmp_path_factory):
    """One demo run in a child process, shared by the demo tests."""
    base = tmp_path_factory.mktemp("demo")
    home = base / "home"
    home.mkdir()
    result = run_module(["demo", "--no-open", "--out", str(base / "out")], cwd=base, home=home)
    return result, base / "out"
