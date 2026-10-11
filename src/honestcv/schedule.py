"""Print-only schedule entries for launchd, cron and Task Scheduler.

Nothing is written and nothing is installed: the person (or their agent, after asking) runs the printed lines.
"""

import plistlib
import re
import shlex
import sys

from honestcv.errors import HonestError
from honestcv.textutil import home_relative
from honestcv.workspace import Workspace

LABEL = "local.honestcv.scan"
PLIST = f"~/Library/LaunchAgents/{LABEL}.plist"
TASK = "honestcv scan"
PLATFORMS = ("macos", "linux", "windows")


def _hours(every: str) -> int | None:
    """None for a daily run, else the hours between runs (1 to 23). 24h is the same as a daily run."""
    text = every.strip().lower()
    found = re.fullmatch(r"(\d{1,2})h", text)
    if text == "1d" or (found and int(found[1]) == 24):
        return None
    if found and 1 <= int(found[1]) < 24:
        return int(found[1])
    raise HonestError(f"'{every}' is not an interval honestcv can schedule.",
                      hint="Use --every 1d for a daily run, or --every Nh with N from 1 to 24, for example --every 6h.")  # fmt: skip


def _clock(at: str | None) -> tuple[int, int]:
    found = re.fullmatch(r"(\d{1,2}):(\d{2})", (at or "08:00").strip())
    if not found or int(found[1]) > 23 or int(found[2]) > 59:
        raise HonestError(f"'{at}' is not a time of day.", hint="Use 24-hour time as HH:MM, for example --at 08:00 or --at 18:30.")
    return int(found[1]), int(found[2])


def _platform(name: str) -> str:
    plat = name.strip().lower()
    if plat == "auto":
        plat = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    if plat not in PLATFORMS:
        raise HonestError(f"'{name}' is not a platform honestcv knows.", hint="Use --platform auto, macos, linux or windows.")
    return plat


def _win_arg(arg: str) -> str:
    """Quote one argument for the command-line rules schtasks applies to /TR."""
    return f'\\"{arg}\\"' if re.search(r'[\s"]', arg) else arg


def schedule_text(ws: Workspace, *, every: str, at: str | None, platform: str, executable: list[str]) -> str:
    """The lines to paste to run `honestcv scan` on a schedule, and the lines that remove it again.

    Paths are absolute because schedulers do not expand ~. With --every Nh the time of day (at) is not used.
    """
    hours, (hour, minute), plat = _hours(every), _clock(at), _platform(platform)
    argv = [*executable, "scan", "--no-open", "--workspace", str(ws.root)]
    log = str(ws.out / "schedule.log")
    when = f"Daily scan at {hour:02d}:{minute:02d}" if hours is None else f"Scan every {hours} hour{'s' if hours != 1 else ''}"
    head = f"{when} for {home_relative(ws.root)}"
    tail = (f"Results land in {home_relative(ws.out)}/ (report-latest.html, matches.csv, latest.json). "
            "honestcv itself sends no notifications.\n")  # fmt: skip
    if plat == "macos":
        entry: dict = {"Label": LABEL, "ProgramArguments": argv, "StandardOutPath": log, "StandardErrorPath": log}
        entry.update({"StartCalendarInterval": {"Hour": hour, "Minute": minute}} if hours is None else {"StartInterval": hours * 3600})
        return (f"{head} (macOS, launchd).\n\n1. Paste this into Terminal to save the schedule file:\n"
                f"mkdir -p ~/Library/LaunchAgents\ncat > {PLIST} <<'PLIST'\n{plistlib.dumps(entry).decode('utf-8')}PLIST\n"
                f"2. Turn it on:\nlaunchctl bootstrap gui/$(id -u) {PLIST}\n\n"
                f"To turn it off again:\nlaunchctl bootout gui/$(id -u)/{LABEL}\nrm {PLIST}\n\n{tail}")  # fmt: skip
    if plat == "linux":
        fields = f"{minute} {hour} * * *" if hours is None else f"0 */{hours} * * *"
        line = f"{fields} {' '.join(shlex.quote(a) for a in argv)} >> {shlex.quote(log)} 2>&1".replace("%", "\\%")
        return (f"{head} (Linux, cron).\n\n1. Run: crontab -e\n2. Paste this line on a new line, then save and close the editor:\n"
                f"{line}\n\nTo turn it off again: run crontab -e and delete that line.\n\n{tail}")  # fmt: skip
    when_args = f"/SC DAILY /ST {hour:02d}:{minute:02d}" if hours is None else f"/SC HOURLY /MO {hours}"
    run = " ".join(_win_arg(a) for a in argv).replace("'", "''")
    return (f"{head} (Windows, Task Scheduler).\n\n1. Open Windows PowerShell, paste this line and press Enter:\n"
            f"schtasks /Create {when_args} /TN \"{TASK}\" /TR '{run}' /F\n\n"
            f"To turn it off again:\nschtasks /Delete /TN \"{TASK}\" /F\n\n{tail}")  # fmt: skip
