"""Shared test setup: no network, today fixed to 2026-10-10, temporary workspaces, an isolated home folder."""

import datetime as dt
import shutil
import socket
import ssl  # noqa: F401  imported before sockets are blocked, so ssl.SSLSocket keeps the real base class
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "src" / "honestcv" / "samples" / "en"
GOLDEN = REPO / "tests" / "golden"
TODAY = dt.date(2026, 10, 10)


class NetworkBlocked(RuntimeError):
    """Raised by any attempt to open a socket or resolve a host name during the tests."""


_MESSAGE = "Tests never touch the network: tests/conftest.py blocks sockets and host lookups."


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise NetworkBlocked(_MESSAGE)


def _blocked(*args, **kwargs):
    raise NetworkBlocked(_MESSAGE)


socket.socket = _BlockedSocket  # type: ignore[misc]
socket.create_connection = _blocked  # type: ignore[assignment]
socket.getaddrinfo = _blocked  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Every test gets an empty home folder, so ~/honestcv on the developer's machine is never touched."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def today() -> dt.date:
    return TODAY


@pytest.fixture
def repo_root() -> Path:
    return REPO


@pytest.fixture
def samples_dir() -> Path:
    return SAMPLES


@pytest.fixture
def golden_dir() -> Path:
    return GOLDEN


@pytest.fixture
def workspace(tmp_path):
    """A fresh, empty workspace made by init_workspace."""
    from honestcv.workspace import Workspace, init_workspace

    root = tmp_path / "ws"
    init_workspace(root)
    return Workspace.for_root(root)


@pytest.fixture
def sample_workspace(tmp_path):
    """A copy of the English sample workspace (Maya Lindqvist, fictional)."""
    from honestcv.workspace import Workspace

    root = tmp_path / "sample"
    shutil.copytree(SAMPLES, root)
    return Workspace.for_root(root)
