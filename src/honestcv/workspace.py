"""Find and create workspaces (default ~/honestcv), workspace paths, atomic writes and the run lock."""

import locale
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from honestcv.config import default_config_text
from honestcv.errors import WorkspaceError

LOCK_STALE_SECONDS = 3600
LEDGER_TEMPLATE = """\
# honestcv claims ledger: each claim says what you did and what proves it.
# It is empty for now. Start with: honestcv ledger draft
# That command fills this file from your CVs; every drafted claim is marked draft: true.
schema_version: 1
person:
  stale_after_days: 365
claims: []
"""


@dataclass(frozen=True)
class Workspace:
    root: Path
    config: Path
    ledger: Path
    cvs: Path
    inbox: Path
    evidence: Path
    out: Path
    state_dir: Path

    @classmethod
    def for_root(cls, root: Path) -> "Workspace":
        r = Path(root).expanduser().resolve()
        return cls(r, r / "honestcv.yml", r / "ledger.yml", r / "cvs", r / "offers" / "inbox", r / "evidence", r / "out", r / ".honestcv")


def default_root() -> Path:
    """~/honestcv (%USERPROFILE%\\honestcv on Windows)."""
    return Path.home() / "honestcv"


def is_workspace(path: Path) -> bool:
    """A folder is a workspace when it holds honestcv.yml or a .honestcv folder."""
    return (path / "honestcv.yml").is_file() or (path / ".honestcv").is_dir()


def find_workspace(start: Path | None = None, explicit: Path | None = None) -> Workspace:
    """explicit if given; else start (default: the current folder) or a parent; else default_root() if it exists."""
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not (root.is_dir() and is_workspace(root)):
            problem = "is not a honestcv workspace (it has no honestcv.yml)" if root.is_dir() else "does not exist"
            raise WorkspaceError(f"The folder {root.name} {problem}.", hint=f"Run: honestcv init {explicit}", path=str(explicit))
        return Workspace.for_root(root)
    here = Path(start).expanduser().resolve() if start is not None else Path.cwd().resolve()
    for folder in (here, *here.parents, default_root()):
        if folder.is_dir() and is_workspace(folder):
            return Workspace.for_root(folder)
    raise WorkspaceError("No workspace here.", hint="Run: honestcv init   (it creates ~/honestcv). Or pass --workspace PATH.")


def locale_csv_delimiter() -> str:
    """";" when the system locale writes decimals with a comma (Excel then expects ";"), else ","."""
    try:
        previous = locale.setlocale(locale.LC_NUMERIC)
        try:
            locale.setlocale(locale.LC_NUMERIC, "")
            point = locale.localeconv().get("decimal_point", ".")
        finally:
            locale.setlocale(locale.LC_NUMERIC, previous)
    except (locale.Error, ValueError):
        return ","
    return ";" if point == "," else ","


def init_workspace(root: Path | None = None, *, force: bool = False, cv: Path | None = None) -> list[Path]:
    """Create a workspace (default ~/honestcv); return the files and folders it created or rewrote.

    Never overwrites a file without force. force backs up honestcv.yml to honestcv.yml.bak and rewrites only
    the template files (honestcv.yml, .gitignore), never cvs/ or ledger.yml. The CSV delimiter written into
    honestcv.yml follows the system locale (locale_csv_delimiter).
    """
    ws = Workspace.for_root(root if root is not None else default_root())
    if cv is not None:
        cv = Path(cv).expanduser().resolve()
        if not cv.is_file():
            raise WorkspaceError(f"The CV file {cv.name} was not found.", path=str(cv),
                                 hint="If the file name has spaces, put it in quotes or drag the file into this window.")  # fmt: skip
    if ws.root.exists() and not ws.root.is_dir():
        raise WorkspaceError(f"{ws.root.name} exists and is not a folder.", hint="Choose another folder name.")
    written = [folder for folder in (ws.root, ws.cvs, ws.inbox, ws.evidence, ws.out) if not folder.is_dir()]
    for folder in written:
        folder.mkdir(parents=True, exist_ok=True)
    config_text = default_config_text()
    if locale_csv_delimiter() == ";":
        config_text = config_text.replace('csv_delimiter: ","', 'csv_delimiter: ";"', 1)
    for path, text, rewritable in ((ws.config, config_text, True), (ws.root / ".gitignore", "*\n", True), (ws.ledger, LEDGER_TEMPLATE, False)):
        if path.exists():
            if not (force and rewritable) or path.read_text(encoding="utf-8", errors="replace") == text:
                continue
            if path == ws.config:
                atomic_write(path.with_name("honestcv.yml.bak"), path.read_bytes())
                written.append(path.with_name("honestcv.yml.bak"))
        atomic_write(path, text)
        written.append(path)
    if cv is not None:
        target = ws.cvs / cv.name
        if not target.exists():
            atomic_write(target, cv.read_bytes())
            written.append(target)
        elif target.read_bytes() != cv.read_bytes():
            raise WorkspaceError(f"cvs/{cv.name} already exists and is a different file.", path=str(target),
                                 hint="Rename your file, or remove the old one from cvs/ first.")  # fmt: skip
    return written


def atomic_write(path: Path, data: str | bytes) -> None:
    """Write to a temporary file in the same folder, then os.replace it into place.

    str is written as UTF-8 with the line ends unchanged. On PermissionError (Windows antivirus, OneDrive)
    os.replace is retried up to 5 times over 1 s.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data.encode("utf-8") if isinstance(data, str) else data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.2)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextmanager
def run_lock(ws: Workspace) -> Iterator[None]:
    """One run per workspace: .honestcv/lock is created with O_EXCL; a lock older than one hour is stale."""
    ws.state_dir.mkdir(parents=True, exist_ok=True)
    lock = ws.state_dir / "lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            stale = time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS
        except FileNotFoundError:
            stale = True
        if not stale:
            raise WorkspaceError("Another honestcv run is using this workspace.", path=str(lock),
                                 hint="Wait for it to finish. If no run is active, delete .honestcv/lock in the workspace.") from None  # fmt: skip
        lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()} {int(time.time())}\n")
        yield
    finally:
        lock.unlink(missing_ok=True)
