"""Command line interface (typer). Maps HonestError to friendly output and exit codes."""

import datetime as dt
import functools
import io
import json
import re
import shutil
import sys
import time
import traceback
import unicodedata
import webbrowser
from pathlib import Path

import typer

from honestcv import __version__
from honestcv.errors import EXIT_BLOCK, EXIT_INTERNAL, EXIT_INVALID, EXIT_PARTIAL, HonestError
from honestcv.textutil import home_relative, safe_name
from honestcv.workspace import Workspace, atomic_write, default_root, find_workspace

app = typer.Typer(pretty_exceptions_enable=False, add_completion=False, help="Job offers matched to your CV, and a claims ledger that keeps tailored CVs truthful.")
ledger_app = typer.Typer(pretty_exceptions_enable=False, add_completion=False, help="Draft and lint your claims ledger.")
app.add_typer(ledger_app, name="ledger")

BUG = "This is a bug in honestcv. Run again with --debug and open an issue. The output may contain lines from your CV or ledger. Remove them before posting."
SPACES = "If the file name has spaces, put it in quotes or drag the file into this window."
NO_IMPORTS = ("No offers from Polish job boards: honestcv does not fetch them. Save their job-alert e-mails into offers/inbox/, "
              "or copy an offer's text and run: honestcv add-offer --paste")  # fmt: skip
EXTRA = {"allow_extra_args": True}


def _utf8() -> None:
    """UTF-8 stdout and stderr, so Polish letters and arrows print under a cp1252 console or a redirect."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass


def _clean(text: str) -> str:
    """Text that came from an offer, a file name or a source, made safe to print. Control characters other than newline
    and tab go: ESC starts the sequences that clear the screen, retitle the window or write to the clipboard."""
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")


def _fail(exc: HonestError) -> None:
    lines = [f"Error: {exc.message}"]
    where = exc.where()
    if where:
        lines.append(f"Where: {home_relative(Path(where)) if Path(where).is_absolute() else where}")
    lines.append(f"Fix: {exc.hint or 'Run: honestcv doctor'}")
    typer.echo(_clean("\n".join(lines)), err=True)


def command(group: typer.Typer, name: str, **settings):
    """Register a command that turns HonestError into a three-part message and other errors into exit code 5."""

    def wrap(fn):
        @functools.wraps(fn)
        def run(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except typer.Exit:
                raise
            except HonestError as exc:
                if kwargs.get("debug"):
                    traceback.print_exc()
                _fail(exc)
                raise typer.Exit(exc.exit_code) from None
            except Exception as exc:
                if kwargs.get("debug"):
                    raise
                typer.echo(f"Error: honestcv stopped because of an internal problem ({type(exc).__name__}).\nFix: {BUG}", err=True)
                raise typer.Exit(EXIT_INTERNAL) from None

        return group.command(name, **settings)(run)

    return wrap


def WS():
    return typer.Option(None, "--workspace", help="Workspace folder. Default: this folder or a parent, else ~/honestcv.")


def DEBUG():
    return typer.Option(False, "--debug", help="Show the full traceback of an error.")


def TODAY():
    return typer.Option(None, "--today", hidden=True)


def _path(arg: str) -> Path:
    return Path(arg).expanduser().resolve()


def _joined(ctx: typer.Context, first: str) -> Path:
    """One path argument; an unquoted name with spaces is joined back when the joined path exists."""
    if not ctx.args:
        return _path(first)
    joined = _path(" ".join([first, *ctx.args]))
    if joined.exists():
        return joined
    raise HonestError(f"honestcv got more than one file name: {' '.join([first, *ctx.args])[:80]}", hint=SPACES)


def _ws(arg: str | None) -> Workspace:
    return find_workspace(None, explicit=_path(arg) if arg else None)


def _day(arg: str | None) -> dt.date | None:
    if arg is None:
        return None
    try:
        return dt.date.fromisoformat(arg)
    except ValueError:
        raise HonestError(f"--today {arg[:20]} is not a date.", hint="Use the form 2026-10-10.") from None


def _open(path: Path, no_open: bool) -> bool:
    if no_open or not sys.stdout.isatty():
        return False
    return webbrowser.open(path.as_uri())


def _show(ws: Workspace, path: Path) -> str:
    try:
        return path.relative_to(ws.root).as_posix()
    except ValueError:
        return home_relative(path)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, version: bool = typer.Option(False, "--version", help="Print the version and exit.")) -> None:
    """Job offers matched to your CV, and a claims ledger that keeps tailored CVs truthful."""
    _utf8()
    if version:
        typer.echo(f"honestcv {__version__}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# ---- demo, init, add-cv, add-offer ----


@command(app, "demo")
def demo(
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the report in a browser."),
    out: str | None = typer.Option(None, "--out", help="A new or empty folder for the demo workspace. Default: a temporary folder."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Offline run on the bundled English sample, on the sample date: scan, report, check of the planted letter."""
    from honestcv.demo import run_demo

    ws, _, _, lines = run_demo(_path(out) if out else None)
    typer.echo("\n".join(lines))
    _open(ws.out / "report-latest.html", no_open)


def _languages(langs: dict[str, str]) -> str:
    return ", ".join(code + ("" if level in ("native", "listed") else f" ({level})") for code, level in langs.items()) or "none found"


def _profile_lines(profile, *, years: bool) -> list[str]:
    out = [f"  {w}" for w in profile.warnings]
    if profile.experience:
        width = max(len(t) for t, _ in profile.experience) + 5
        out += ["Job titles and dates found under experience headings:", *(f"  {t:<{width}}{d}" for t, d in profile.experience)]
        if years and profile.years is not None:
            out.append(f'  about {profile.years:g} years in total (used only for "N+ years" requirements)')
    else:
        out.append("No job titles with dates were found under experience headings.")
    if profile.level is None:
        out.append("Set profile.level in honestcv.yml to get level matching. A guess from CV dates is wrong if you are changing careers.")
    return out


def _read_cv(path: Path, ws: Workspace, today: dt.date | None):
    from honestcv.config import load_config
    from honestcv.profile import extract_profile

    return extract_profile(path, load_config(ws.config).profile, today=today)


@command(app, "init", context_settings=EXTRA)
def init(
    ctx: typer.Context,
    folder: str | None = typer.Argument(None, metavar="[DIR]", help="Folder to create. Default: ~/honestcv."),
    cv: str | None = typer.Option(None, "--cv", help="Your CV (.pdf .docx .md .txt), copied into cvs/."),
    force: bool = typer.Option(False, "--force", help="Rewrite honestcv.yml (old copy kept as honestcv.yml.bak)."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Create a workspace (default ~/honestcv); optionally add a CV."""
    from honestcv.profile import load_cv_text
    from honestcv.workspace import init_workspace

    extra = [*([folder] if folder else []), *ctx.args]
    if cv and extra and _path(" ".join([cv, *extra])).is_file():
        cv, folder, extra = " ".join([cv, *extra]), None, []
    elif len(extra) > 1:
        raise HonestError(f"honestcv got more than one folder name: {' '.join(extra)[:80]}", hint=SPACES)
    cv_path = _path(cv) if cv else None
    if cv_path is not None:
        if not cv_path.is_file():
            raise HonestError(f"The CV file {cv_path.name} was not found.", hint=SPACES, path=home_relative(cv_path))
        load_cv_text(cv_path)  # refuses .doc, protected and image-only files before anything is copied
    root = _path(folder) if folder else _path(workspace) if workspace else default_root()
    written = init_workspace(root, force=force, cv=cv_path)
    ws = Workspace.for_root(root)
    items = [(ws.config, "honestcv.yml", "settings; every line optional, defaults work"),
             (ws.cvs, "cvs/", "your CVs (.pdf .docx .md .txt)" + (f"; {cv_path.name} copied here" if cv_path and ws.cvs / cv_path.name in written else "")),
             (ws.ledger, "ledger.yml", "your claims ledger, empty (start with: honestcv ledger draft)"),
             (ws.inbox, "offers/inbox/", "drop job-alert .eml, saved .html, .csv or .json files here"),
             (ws.evidence, "evidence/", "optional files your ledger points to"), (ws.out, "out/", "reports land here"),
             (ws.root / ".gitignore", ".gitignore", 'contains "*": nothing in this folder is meant for git')]  # fmt: skip
    if ws.root in written:
        lines = [f"Created {home_relative(ws.root)}/"] + [f"  {name:<16} {what}" for _, name, what in items]
    else:
        changed = [f"  {name:<16} {what}" for path, name, what in items if path in written or (path == ws.cvs and cv_path and ws.cvs / cv_path.name in written)]
        lines = [f"Updated {home_relative(ws.root)}/", *changed] if changed else [f"{home_relative(ws.root)}/ is already set up; nothing was changed."]
    if ws.config.with_name("honestcv.yml.bak") in written:
        lines.append("Your previous settings are in honestcv.yml.bak.")
    if cv_path is not None:
        profile = _read_cv(ws.cvs / cv_path.name, ws, _day(today))
        pages = f"{profile.pages} page{'s' if profile.pages != 1 else ''}, " if profile.pages else ""
        lines.append(f"Read cvs/{cv_path.name}: {pages}{len(profile.skills)} skills, languages {_languages(profile.languages)}.")
        lines += _profile_lines(profile, years=True)
    elif not any(ws.cvs.iterdir()):
        lines.append("Put your CV into cvs/ or run: honestcv add-cv FILE")
    shown = home_relative(ws.root)
    custom = ws.root != default_root().expanduser().resolve()
    lines.append("Next: honestcv scan" + (f' --workspace "{shown}"' if custom and " " in shown else f" --workspace {shown}" if custom else ""))
    typer.echo("\n".join(lines))


@command(app, "add-cv", context_settings=EXTRA)
def add_cv(
    ctx: typer.Context, file: str = typer.Argument(..., metavar="FILE", help="Your CV (.pdf .docx .md .txt)."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Copy a CV into cvs/ and print what was read."""
    from honestcv.profile import load_cv_text

    ws, src = _ws(workspace), _joined(ctx, file)
    if not src.is_file():
        raise HonestError(f"The file {src.name} was not found.", hint=SPACES, path=home_relative(src))
    load_cv_text(src)
    target = ws.cvs / src.name
    if target.exists() and target.read_bytes() != src.read_bytes():
        raise HonestError(f"cvs/{src.name} already exists and is a different file.", hint="Rename your file, or remove the old one from cvs/ first.")
    note = f"Already in cvs/{src.name}." if target.exists() else f"Copied to cvs/{src.name}."
    atomic_write(target, src.read_bytes())
    profile = _read_cv(target, ws, _day(today))
    skills = ", ".join(profile.skills[:5]) + (", ..." if len(profile.skills) > 5 else "")
    lines = [note, f"Found: {len(profile.skills)} skills" + (f" ({skills})" if skills else "") + f", languages {_languages(profile.languages)}.",
             *_profile_lines(profile, years=False), "Every scan scores each offer against every CV in cvs/ and shows the best one.",
             "Wrong skill? Add or remove it in honestcv.yml: profile.skills_add / profile.skills_remove."]  # fmt: skip
    typer.echo("\n".join(lines))


_RECORD = ("title", "company", "url", "location", "countries", "remote", "contract", "level", "skills", "skills_nice", "languages",
           "description", "posted_at", "valid_through", "requisition_id")  # fmt: skip


def _record(offer) -> dict:
    record = {k: v for k, v in offer.model_dump(mode="json").items() if k in _RECORD and v not in (None, "", [])}
    if offer.salary:
        record.update({k: v for k, v in (("salary_min", offer.salary.min), ("salary_max", offer.salary.max), ("currency", offer.salary.currency),
                                         ("salary_unit", offer.salary.unit)) if v is not None})  # fmt: skip
    return record


def _save_offers(ws: Workspace, offers: list, day: dt.date, name: str, *, reuse: bool = False) -> Path:
    """offers/inbox/added-<date>-<name>.json in the import schema (docs/import.md). With reuse, a file that already
    holds exactly these offers is the answer."""
    text = json.dumps([_record(o) for o in offers], indent=1, ensure_ascii=False) + "\n"
    base, n = f"added-{day.isoformat()}-{safe_name(name)}", 2
    path = ws.inbox / f"{base}.json"
    while path.exists() and not (reuse and path.read_text(encoding="utf-8") == text):
        path, n = ws.inbox / f"{base}-{n}.json", n + 1
    atomic_write(path, text)
    return path


def _save_offer(ws: Workspace, offer, day: dt.date) -> Path:
    return _save_offers(ws, [offer], day, offer.title)


@command(app, "add-offer", context_settings=EXTRA)
def add_offer(
    ctx: typer.Context,
    file: str | None = typer.Argument(None, metavar="[FILE|TEXT]", help="An offer file (.eml .html .csv .json .txt .md), or the text with --text."),
    paste: bool = typer.Option(False, "--paste", help="Read the offer text from the clipboard."),
    text: bool = typer.Option(False, "--text", help="Offer text as the argument; with no argument it is read from stdin."),
    url: str | None = typer.Option(None, "--url", help="Store a link as an offer with no text. The page is never fetched."),
    title: str | None = typer.Option(None, "--title", help="Offer title. Default: the first heading or line."),
    company: str | None = typer.Option(None, "--company", help="Company name."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Import offers: a file, the clipboard (--paste), text (--text) or a link (--url)."""
    from honestcv import importers
    from honestcv.sources.http import UNLISTED_HOST_MESSAGE

    ws, day = _ws(workspace), _day(today) or dt.date.today()
    if [bool(file) and not text, paste, text, bool(url)].count(True) != 1:
        raise HonestError("add-offer needs exactly one of: FILE, --paste, --text or --url.", hint="For example: honestcv add-offer --paste")
    if url:
        offer = importers.import_url(url, title=title)
        typer.echo(_clean(f'Saved the link as an offer with no text: "{offer.title}". It is listed under "Needs the full text" after the next scan.'))
        typer.echo(f"{_show(ws, _save_offer(ws, offer, day))}\n{UNLISTED_HOST_MESSAGE}")
        return
    if file and not text:
        src = _joined(ctx, file)
        offers, notes = importers.read_path(src, day)
        if src.suffix.lower() == ".eml":  # the offers are kept, the e-mail with its sender, recipients and headers is not
            if not offers:
                raise HonestError(f"honestcv found no offers in {src.name}.", hint="Copy an offer's text and run: honestcv add-offer --paste")
            saved = _save_offers(ws, offers, day, src.stem, reuse=True)
            typer.echo(_clean("\n".join([f"Imported {len(offers)} offer{'s' * (len(offers) != 1)} from {src.name}. They join the next scan.",
                                         f"Saved as {_show(ws, saved)}. The e-mail itself was not copied.", *notes])))  # fmt: skip
            return
        target, n = ws.inbox / src.name, 2
        while target.exists() and target.read_bytes() != src.read_bytes():
            target, n = ws.inbox / f"{src.stem}-{n}{src.suffix}", n + 1
        atomic_write(target, src.read_bytes())
        typer.echo(_clean("\n".join([f"Copied to {_show(ws, target)}: {len(offers)} offer{'s' * (len(offers) != 1)}. They join the next scan.", *notes])))
        return
    where = "the clipboard" if paste else "your text"
    body = importers.read_clipboard() if paste else " ".join([file, *ctx.args]) if file else sys.stdin.read()
    if not body.strip():
        raise HonestError(f"There is no text in {where}.", hint="Copy the offer text first (Ctrl+C / Cmd+C), then run the command again.")
    offer = importers.import_text(body, title=title, company=company)
    origin = "" if title else " (title from the first line)"
    path = _save_offer(ws, offer, day)
    typer.echo(_clean(f'Imported 1 offer from {where}: "{offer.title}"{origin}. It joins the next scan.\nSaved as {_show(ws, path)}'))


# ---- ledger ----


@command(ledger_app, "draft")
def ledger_draft(
    out: str | None = typer.Option(None, "--out", help="Write the draft to this file instead of ledger.yml."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Draft a starting ledger from your CVs; every claim is marked draft: true."""
    from honestcv.config import load_config
    from honestcv.ledger import draft_ledger, load_ledger
    from honestcv.profile import load_cv_text, load_profiles

    ws, day = _ws(workspace), _day(today) or dt.date.today()
    target = _path(out) if out else ws.ledger
    if target.exists() and (target.suffix.lower() not in (".yml", ".yaml") or load_ledger(target) is not None):
        raise HonestError(f"{_show(ws, target)} already has claims, so honestcv will not touch it.", hint="Run: honestcv ledger draft --out ledger.draft.yml")
    profiles = load_profiles(ws, load_config(ws.config), today=day)
    if not profiles:
        raise HonestError("There is no CV in cvs/ to draft from.", hint="Put your CV into cvs/ or run: honestcv add-cv FILE")
    text = draft_ledger(profiles, {p.cv_id: load_cv_text(ws.cvs / p.cv_id).text for p in profiles}, today=day)
    atomic_write(target, text)
    count = text.count("\n  - id: ")
    typer.echo("\n".join([
        f"Drafted {count} claims from {len(profiles)} CV{'s' * (len(profiles) != 1)} into {_show(ws, target)}. All are marked draft: true.",
        "A draft claim does not count as ledger backing until you confirm it. Tailoring and the check accept it only word for word, as it stands in your CV.",
        "To confirm a claim, add its state (built, tested, shipped, used, ...), its evidence and the sentence you want to use.",
        "Each claim has comments showing what to fill. Then run: honestcv ledger lint"]))  # fmt: skip


@command(ledger_app, "lint")
def ledger_lint(workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY()) -> None:
    """Check ledger.yml: evidence for each state, regexes, allowed sentences, review dates."""
    from honestcv.ledger import lint_ledger, load_ledger

    ws, day = _ws(workspace), _day(today) or dt.date.today()
    ledger = load_ledger(ws.ledger)
    if ledger is None:
        typer.echo("ledger.yml: 0 claims. Start with: honestcv ledger draft")
        return
    issues = lint_ledger(ledger, ws.root, day)
    drafts = sum(c.draft for c in ledger.claims)
    lines = [f"ledger.yml: {len(ledger.claims)} claims · {len(ledger.claims) - drafts} confirmed · {drafts} draft"]
    shown: dict[str, int] = {}
    for issue in sorted(issues, key=lambda i: (i.severity != "ERROR", i.line or 0, i.path)):
        same = issue.message.split(": ", 1)[-1] if issue.path.endswith(".draft") else None
        if same and same in shown:
            shown[same] += 1
            continue
        if same:
            shown[same] = 0
        lines.append(f"{issue.severity:<6} " + (f"line {issue.line}  " if issue.line else "") + issue.message)
    lines += [f"       ({n} more draft claim{'s' * (n != 1)} with the same WARN)" for n in shown.values() if n]
    errors = sum(i.severity == "ERROR" for i in issues)
    lines.append("Tailoring is blocked until there are no ERROR lines." if errors else "No ERROR lines: tailoring and the check can use this ledger.")
    typer.echo("\n".join(lines))
    if errors:
        raise typer.Exit(EXIT_INVALID)


# ---- scan, report, tailor, check ----


def _interval(text: str) -> int:
    found = re.fullmatch(r"(\d{1,4})([mh])", text.strip().lower())
    seconds = int(found[1]) * (60 if found[2] == "m" else 3600) if found else 0
    if seconds < 1800:
        raise HonestError(f"--every {text[:20]} is not an interval honestcv repeats.", hint="Use minutes or hours, at least 30m, for example --every 6h.")
    return seconds


def _scan_lines(ws: Workspace, run, opened: bool) -> list[str]:
    from honestcv.demo import gate_summary
    from honestcv.report.matches import build_view, ranked

    view = build_view(run)
    parts = []
    for s in run.sources:
        if s.id == "inbox":
            parts.append(f"inbox {s.message.split('; ')[0]}" if s.state == "ok" else f"inbox {s.state}")
        else:
            parts.append(f"{s.id} {s.state}" + (f" {s.offers}" if s.state in ("ok", "cached") else ""))
    filtered = sum(run.gate_counts.values())
    lines = [f"Sources   {' · '.join(parts)}",
             f"Gates     filtered {filtered}" + (f": {gate_summary(run.gate_counts).replace(', ', ' · ')}" if filtered else ""),
             f"Scored    {view.scored} offers x {len(run.cvs)} CV{'s' * (len(run.cvs) != 1)} · surfaced {len(view.cards)} · "
             f"needs the full text {len(view.needs_text)} · new since last scan {view.new}",
             f"Status    {run.status}" + ("" if run.status != "FAIL" else " · Could not collect offers")]  # fmt: skip
    lines += [f"          {s.id}: {s.message}" for s in view.failing]
    day = run.today.isoformat()
    lines += [f"Report    out/report-latest.html  (also out/report-{day}.md)" + (", opening in your browser" if opened else ""),
              f"Matches   out/matches.csv  +{len(ranked(run))} rows (one per surfaced offer) · out/latest.json"]  # fmt: skip
    if not any(s.id == "inbox" and s.offers for s in run.sources):
        lines.append(NO_IMPORTS)
    return [_clean(line) for line in lines]


@command(app, "scan")
def scan(
    offline: bool = typer.Option(False, "--offline", help="Use only the inbox and the cache; make no requests."),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the report in a browser."),
    all_: bool = typer.Option(False, "--all", help="Also list offers below the surface threshold."),
    json_: bool = typer.Option(False, "--json", help="Print the run as JSON (schema honestcv.scan.v1) and open nothing."),
    every: str | None = typer.Option(None, "--every", help="Repeat in the foreground until Ctrl-C, for example 6h (at least 30m)."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Collect, score, write the report and open it when stdout is a terminal."""
    from honestcv.config import load_config
    from honestcv.pipeline import run_scan

    ws, day = _ws(workspace), _day(today)
    pause = _interval(every) if every else None
    first = True
    while True:
        run = run_scan(ws, load_config(ws.config), offline=offline, today=day or dt.date.today(), pinned=day is not None, show_below=all_)
        if json_:
            typer.echo(run.model_dump_json(by_alias=True, indent=1))
        else:
            opened = first and _open(ws.out / "report-latest.html", no_open)
            typer.echo("\n".join(_scan_lines(ws, run, opened)))
        if pause is None:
            break
        first = False
        typer.echo(f"Next scan at {(dt.datetime.now() + dt.timedelta(seconds=pause)):%H:%M}. Press Ctrl-C to stop.", err=json_)
        try:
            time.sleep(pause)
        except KeyboardInterrupt:
            typer.echo("Stopped.", err=json_)
            return
    if run.status != "PASS":
        raise typer.Exit(EXIT_PARTIAL)


@command(app, "report")
def report(
    open_: bool = typer.Option(False, "--open", help="Open the report in a browser."),
    run_id: str | None = typer.Option(None, "--run", help="A saved run id from out/runs/. Default: the latest run."),
    all_: bool = typer.Option(False, "--all", help="Also list offers below the surface threshold."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Re-render a saved run."""
    from honestcv.pipeline import load_run, write_reports

    ws = _ws(workspace)
    run = load_run(ws, run_id)
    write_reports(run, ws, show_below=all_)
    opened = open_ and webbrowser.open((ws.out / "report-latest.html").as_uri())
    typer.echo(f"Report    out/report-latest.html  (also out/report-{run.today.isoformat()}.md), run {run.run_id}" + (", opening in your browser" if opened else ""))


def _pick(ws: Workspace, which: str, cv_id: str | None = None):
    """(run, offer, match) for an offer number in the latest report, or an offer id."""
    from honestcv.pipeline import load_run
    from honestcv.report.matches import build_view

    run = load_run(ws)
    cards = build_view(run).cards  # the numbering of the report, duplicates folded into one card
    if which.isdigit():
        if not 1 <= int(which) <= len(cards):
            raise HonestError(f"There is no offer {which} in the latest report; it ranks {len(cards)}.", hint="Run: honestcv report --open, and use a number from it.")
        offer = cards[int(which) - 1].offer
    else:
        offer = next((o for o in run.offers if o.id == which.strip()), None)
        if offer is None:
            raise HonestError(f"There is no offer with the id {which[:20]} in the latest scan.", hint="Use the offer number or id shown in the report.")
    results = sorted((r for r in run.results if r.offer_id == offer.id and (cv_id is None or r.cv_id == cv_id)), key=lambda r: (-r.match_pct, r.cv_id))
    if not results:
        raise HonestError(f"The latest scan has no result for {cv_id} on this offer.", hint=f"Use one of: {', '.join(run.cvs)}")
    if results[0].gate:
        raise HonestError(f"This offer was filtered out: {results[0].gate.detail}", hint="Pick an offer from the ranked list in the report.")
    return run, offer, results[0]


def _ledger_or_stop(ws: Workspace):
    from honestcv.ledger import load_ledger

    ledger = load_ledger(ws.ledger)
    if ledger is None:
        raise HonestError("Your ledger is empty.", hint="Run: honestcv ledger draft", path="ledger.yml")
    return ledger


@command(app, "tailor")
def tailor(
    offer_ref: str = typer.Argument(..., metavar="OFFER", help="The offer number in the latest report, or an offer id."),
    cv: str | None = typer.Option(None, "--cv", help="Use this CV instead of the best one, for example maya-analytics.md."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Highlights for one offer from your ledger sentences, checked."""
    from honestcv.config import load_config
    from honestcv.profile import load_profiles
    from honestcv.tailor import tailor as run_tailor

    ws, day = _ws(workspace), _day(today) or dt.date.today()
    _, offer, match = _pick(ws, offer_ref, cv)
    ledger = _ledger_or_stop(ws)
    profile = next((p for p in load_profiles(ws, load_config(ws.config), today=day) if p.cv_id == match.cv_id), None)
    if profile is None:
        raise HonestError(f"cvs/{match.cv_id} is no longer in the workspace.", hint="Run: honestcv scan, then tailor again.")
    result = run_tailor(offer, match, profile, ledger, ws, today=day)
    out = ws.root / result.out_dir
    cited = (out / "highlights.cited.md").read_text(encoding="utf-8")
    claims = len(set(re.findall(r"<!--\s*c:\s*([^,>\s]+)", cited)))
    bullets = sum(1 for line in (out / "highlights.md").read_text(encoding="utf-8").splitlines() if line.startswith("- "))
    skills = [g for g in result.gaps if ': "' not in g]
    unbacked = f"{len(skills)} required skill{'s have' if len(skills) != 1 else ' has'} no ledger backing: {', '.join(skills)} (listed in gaps.md, added nowhere)" if skills else "every required skill has ledger backing"
    removed = f"; removed {len(result.removed)} blocked line{'s' * (len(result.removed) != 1)}, see check.md" if result.removed else ""
    which = "CV" if cv else "best CV"
    typer.echo(_clean("\n".join([
        f"Offer   {offer.title}" + (f" · {offer.company}" if offer.company else "") + f" ({match.match_pct}%, {match.layer}, {which} {match.cv_id})",
        "Mode    deterministic: selects and orders your ledger sentences, never rewrites them",
        f"Picked  {claims} claim{'s' * (claims != 1)} · {unbacked}", f"Check   {result.check.verdict} (read back from disk){removed}",
        f"Wrote {bullets} bullet{'s' * (bullets != 1)} to paste into your CV", f"        {result.out_dir}/",
        "        highlights.md  highlights.cited.md  check.md  gaps.md",
        "Before you send the final CV or letter, run: honestcv check FILE"])))  # fmt: skip


@command(app, "check", context_settings=EXTRA)
def check(
    ctx: typer.Context, file: str = typer.Argument(..., metavar="FILE", help="Any document: .md .txt .pdf .docx."),
    offer_ref: str | None = typer.Option(None, "--offer", help="Also list what this offer asks for that your ledger does not back."),
    json_: bool = typer.Option(False, "--json", help="Print the result as JSON (schema honestcv.check.v1)."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Claims check of any document against your ledger; report only, the file is never changed."""
    from honestcv.checker import check_document, render_check
    from honestcv.profile import SUPPORTED, load_cv_text

    ws, day, path = _ws(workspace), _day(today) or dt.date.today(), _joined(ctx, file)
    if not path.is_file():
        raise HonestError(f"The file {path.name} was not found.", hint=SPACES, path=home_relative(path))
    if path.suffix.lower() not in SUPPORTED:
        load_cv_text(path)  # raises the "Save it as PDF or DOCX" message
    ledger = _ledger_or_stop(ws)
    offer = match = None
    if offer_ref:
        _, offer, match = _pick(ws, offer_ref)
    result = check_document(path, ledger, offer=offer, requirements=match.requirements if match else None, ws_root=ws.root, today=day)
    if json_:
        typer.echo(result.model_dump_json(by_alias=True, indent=1))
    else:
        typer.echo(_clean(render_check(result).rstrip("\n")))
        typer.echo("Your file was not changed." + (" Fix the lines yourself, then run the check again before sending." if result.verdict != "PASS" else ""))
        if result.verdict == "BLOCK":
            typer.echo(f"Exit code {EXIT_BLOCK} (BLOCK)")
    if result.verdict == "BLOCK":
        raise typer.Exit(EXIT_BLOCK)


# ---- sources, doctor, schedule ----


@command(app, "sources")
def sources(workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY()) -> None:
    """Registry table: verdict, terms URL, verified-at, rates, attribution, last status."""
    from honestcv.config import load_config
    from honestcv.pipeline import load_run
    from honestcv.sources import registry_table

    statuses, enabled = None, None
    try:
        ws = _ws(workspace)
        enabled = load_config(ws.config).search.sources
        statuses = [s for s in load_run(ws).sources if s.id != "inbox"]
    except HonestError:
        pass
    typer.echo(registry_table(statuses).rstrip("\n"))
    if enabled is not None:
        typer.echo(f"Enabled in honestcv.yml: {', '.join(enabled) or 'none'}. Greenhouse, Lever and Ashby run once you list companies.")
    typer.echo("honestcv fetches from no other host. Offers from other sites come in by file or paste: honestcv add-offer --paste")


@command(app, "doctor")
def doctor(
    online: bool = typer.Option(False, "--online", help="Also test the sources over the network."),
    json_: bool = typer.Option(False, "--json", help="Print the checks as JSON."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Read-only health check; exit 0 without an ERROR line, else 1."""
    from honestcv.doctor import doctor_exit_code, run_doctor

    checks = run_doctor(None, online=online, workspace=_path(workspace) if workspace else None)
    errors, warns = sum(c.level == "ERROR" for c in checks), sum(c.level == "WARN" for c in checks)
    if json_:
        typer.echo(json.dumps({"schema": "honestcv.doctor.v1", "errors": errors, "warnings": warns, "checks": [c.model_dump() for c in checks]}, indent=1, ensure_ascii=False))
    else:
        lines = [c.message if c.level == "INFO" else f"{c.level:<6}{c.name:<12}{c.message}" for c in checks]
        typer.echo("\n".join([*lines, f"{errors} error{'s' * (errors != 1)} · {warns} warning{'s' * (warns != 1)}"]))
    raise typer.Exit(doctor_exit_code(checks))


@command(app, "schedule")
def schedule(
    every: str = typer.Option("1d", "--every", help="1d for a daily run, or Nh with N from 1 to 24."),
    at: str | None = typer.Option(None, "--at", help="Time of day for a daily run, HH:MM. Default 08:00."),
    platform: str = typer.Option("auto", "--platform", help="auto, macos, linux or windows."),
    workspace: str | None = WS(), debug: bool = DEBUG(), today: str | None = TODAY(),
) -> None:
    """Print a ready schedule entry for this computer and how to remove it. Writes and installs nothing."""
    from honestcv.schedule import schedule_text

    found = shutil.which("honestcv")
    executable = [str(Path(found).absolute())] if found else [sys.executable, "-m", "honestcv"]
    typer.echo(schedule_text(_ws(workspace), every=every, at=at, platform=platform, executable=executable).rstrip("\n"))
