"""schedule_text prints a ready entry for launchd, cron or Task Scheduler and writes nothing."""

import plistlib
import re
import sys

import pytest

from honestcv.errors import HonestError
from honestcv.schedule import schedule_text
from honestcv.workspace import Workspace

EXE = ["/opt/honestcv-tool/bin/honestcv"]


def snapshot(*roots):
    return sorted((str(p), p.stat().st_size) for root in roots for p in [root, *root.rglob("*")])


def make(tmp_path, name="ws") -> Workspace:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return Workspace.for_root(root)


def plist_of(text: str) -> dict:
    start, end = text.index("<?xml"), text.index("</plist>") + len("</plist>")
    return plistlib.loads(text[start:end].encode("utf-8"))


@pytest.mark.parametrize("platform", ["macos", "linux", "windows"])
def test_every_platform_names_the_command_and_the_removal(tmp_path, platform):
    ws = make(tmp_path)
    text = schedule_text(ws, every="1d", at="08:00", platform=platform, executable=EXE)
    assert EXE[0] in text
    assert " scan " in text and "--no-open" in text
    assert f"--workspace {ws.root}" in text or str(ws.root) in text
    assert "To turn it off again" in text
    assert "sends no notifications" in text
    assert "&&" not in text
    removal = {"macos": "launchctl bootout", "linux": "delete that line", "windows": "schtasks /Delete"}[platform]
    assert removal in text


def test_macos_plist_parses_and_carries_the_arguments(tmp_path):
    ws = make(tmp_path)
    text = schedule_text(ws, every="1d", at="18:30", platform="macos", executable=EXE)
    entry = plist_of(text)
    assert entry["Label"] == "local.honestcv.scan"
    assert entry["ProgramArguments"] == [*EXE, "scan", "--no-open", "--workspace", str(ws.root)]
    assert entry["StartCalendarInterval"] == {"Hour": 18, "Minute": 30}
    assert entry["StandardOutPath"] == entry["StandardErrorPath"] == str(ws.out / "schedule.log")
    assert "cat > ~/Library/LaunchAgents/local.honestcv.scan.plist <<'PLIST'" in text
    assert "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.honestcv.scan.plist" in text
    assert "launchctl bootout gui/$(id -u)/local.honestcv.scan" in text
    assert "rm ~/Library/LaunchAgents/local.honestcv.scan.plist" in text
    assert text.count("\nPLIST\n") == 1  # the heredoc closes exactly once


def test_macos_plist_escapes_odd_workspace_names(tmp_path):
    ws = make(tmp_path, "Tom & Jerry" if sys.platform == "win32" else "Tom & Jerry <jobs>")  # Windows forbids < > in names
    entry = plist_of(schedule_text(ws, every="1d", at=None, platform="macos", executable=[sys.executable, "-m", "honestcv"]))
    assert entry["ProgramArguments"][-1] == str(ws.root)
    assert entry["ProgramArguments"][:3] == [sys.executable, "-m", "honestcv"]


def test_default_time_is_eight_in_the_morning(tmp_path):
    ws = make(tmp_path)
    assert plist_of(schedule_text(ws, every="1d", at=None, platform="macos", executable=EXE))["StartCalendarInterval"] == {"Hour": 8, "Minute": 0}
    assert "0 8 * * * " in schedule_text(ws, every="1d", at="08:00", platform="linux", executable=EXE)
    assert "30 18 * * * " in schedule_text(ws, every="1d", at="18:30", platform="linux", executable=EXE)
    assert "/SC DAILY /ST 06:05" in schedule_text(ws, every="1d", at="6:05", platform="windows", executable=EXE)


def test_every_six_hours(tmp_path):
    ws = make(tmp_path)
    assert plist_of(schedule_text(ws, every="6h", at=None, platform="macos", executable=EXE))["StartInterval"] == 21600
    assert "0 */6 * * * " in schedule_text(ws, every="6h", at="08:00", platform="linux", executable=EXE)
    windows = schedule_text(ws, every="6h", at="08:00", platform="windows", executable=EXE)
    assert "/SC HOURLY /MO 6" in windows and "/ST" not in windows
    assert "Scan every 6 hours" in windows
    assert "Scan every 1 hour " in schedule_text(ws, every="1h", at=None, platform="linux", executable=EXE)


def test_a_full_day_in_hours_is_a_daily_run(tmp_path):
    ws = make(tmp_path)
    text = schedule_text(ws, every="24h", at="07:15", platform="windows", executable=EXE)
    assert "/SC DAILY /ST 07:15" in text and "HOURLY" not in text


@pytest.mark.parametrize("every, at, platform", [
    ("0h", None, "linux"), ("25h", None, "linux"), ("2d", None, "linux"), ("30m", None, "linux"), ("six", None, "linux"),
    ("1d", "8am", "linux"), ("1d", "25:00", "linux"), ("1d", "08:60", "linux"), ("6h", "8am", "linux"),
    ("1d", None, "amiga"),
])  # fmt: skip
def test_bad_values_raise_with_a_fix_hint(tmp_path, every, at, platform):
    with pytest.raises(HonestError) as caught:
        schedule_text(make(tmp_path), every=every, at=at, platform=platform, executable=EXE)
    assert caught.value.hint and "--" in caught.value.hint


def test_auto_picks_a_known_platform(tmp_path, monkeypatch):
    ws = make(tmp_path)
    for name, expect in (("darwin", "launchd"), ("win32", "Task Scheduler"), ("linux", "cron")):
        monkeypatch.setattr(sys, "platform", name)
        assert expect in schedule_text(ws, every="1d", at=None, platform="auto", executable=EXE)


POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="a cron line carries a Linux or macOS path")


@POSIX_ONLY
def test_cron_line_is_shell_safe(tmp_path):
    ws = make(tmp_path, "My jobs 100%")
    text = schedule_text(ws, every="1d", at="08:00", platform="linux", executable=["/opt/my tools/honestcv"])
    line = next(row for row in text.splitlines() if row.startswith("0 8 * * * "))
    assert "'/opt/my tools/honestcv'" in line
    assert "100\\%" in line and "100%" not in line.replace("100\\%", "")
    assert line.endswith("/out/schedule.log' 2>&1")


def crt_split(cmd: str) -> list[str]:
    """Split a command line the way the Microsoft C runtime does (2n backslashes + quote rule)."""
    args, cur, in_quote, have, i = [], [], False, False, 0
    while i < len(cmd):
        c = cmd[i]
        if c == "\\":
            n = 0
            while i < len(cmd) and cmd[i] == "\\":
                n, i = n + 1, i + 1
            if i < len(cmd) and cmd[i] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i += 1
            else:
                cur.append("\\" * n)
            have = True
        elif c == '"':
            in_quote, have, i = not in_quote, True, i + 1
        elif c.isspace() and not in_quote:
            if have:
                args.append("".join(cur))
                cur, have = [], False
            i += 1
        else:
            cur.append(c)
            have, i = True, i + 1
    if have:
        args.append("".join(cur))
    return args


def test_windows_line_survives_powershell_and_the_command_line_rules(tmp_path):
    exe = ["C:\\Program Files\\honestcv tool\\honestcv.exe"]
    ws = make(tmp_path / "Jan K", "O'Brien jobs")
    text = schedule_text(ws, every="1d", at="08:00", platform="windows", executable=exe)
    line = next(row for row in text.splitlines() if row.startswith("schtasks /Create"))
    assert line.startswith('schtasks /Create /SC DAILY /ST 08:00 /TN "honestcv scan" /TR ')
    literal = re.search(r" /TR '(.*)' /F$", line).group(1)
    argument = literal.replace("''", "'")  # PowerShell single-quoted string
    seen_by_schtasks = crt_split(f'/TR "{argument}" /F')  # Windows PowerShell 5.1 wraps an argument with spaces in quotes
    assert seen_by_schtasks[0] == "/TR" and seen_by_schtasks[2] == "/F"
    assert crt_split(seen_by_schtasks[1]) == [*exe, "scan", "--no-open", "--workspace", str(ws.root)]
    assert 'schtasks /Delete /TN "honestcv scan" /F' in text


def test_prints_only_nothing_is_written(tmp_path, _isolated_home):
    ws = make(tmp_path)
    before = snapshot(tmp_path, _isolated_home)
    for platform in ("macos", "linux", "windows"):
        schedule_text(ws, every="1d", at="08:00", platform=platform, executable=EXE)
    assert snapshot(tmp_path, _isolated_home) == before
    assert not (_isolated_home / "Library").exists()


@POSIX_ONLY
def test_the_workspace_is_shown_with_a_tilde_but_commands_stay_absolute(tmp_path, _isolated_home):
    ws = Workspace.for_root(_isolated_home / "honestcv")
    text = schedule_text(ws, every="1d", at="08:00", platform="linux", executable=EXE)
    assert text.startswith("Daily scan at 08:00 for ~/honestcv (Linux, cron).")
    assert "Results land in ~/honestcv/out/" in text
    assert f"--workspace {ws.root}" in text
