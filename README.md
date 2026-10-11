# honestcv

Job offers matched to your CV, and a claims ledger that keeps tailored CVs truthful.

honestcv reads public job APIs and job offers you save, scores each offer against your CV with one rule set you can read, and checks every line the rules recognise as a claim, in a CV or a cover letter, against a ledger of what you actually did and what proves it. It runs on your computer. It makes no model calls.

## Why this exists

Tools that tailor a CV to an offer tend to make claims stronger than the facts. One case is described in [career-ops issue #1411](https://github.com/career-ops-hq/career-ops/issues/1411): tailoring turned work the user directed a vendor to build into work the user wrote. Nobody lied on purpose, and the sentence still went out.

honestcv keeps a ledger of your claims. Each claim records what state the work reached (built, tested, shipped, used, and so on), the evidence for it, who did it, and the sentences you are happy to send. The checker reads any document and flags lines that claim more than the ledger backs: a stronger verb, a bigger number, a wider scope, an outcome nobody measured, or authorship you do not have. It does this with fixed rules, so the same line always gets the same answer. A line that makes a claim in words the rules do not know is not checked, so a PASS means that no claim the rules recognise went beyond your ledger. It does not mean the whole document is true.

## Start here

You need uv, a small tool that installs Python programs. Do this once per computer.

- macOS: press Cmd+Space, type Terminal, press Enter, then run:
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: open Start, type PowerShell, press Enter, then run:
  `winget install --id=astral-sh.uv -e`
- Linux: open a terminal, then run:
  `curl -LsSf https://astral.sh/uv/install.sh | sh`

Close the window and open a new one. Then install honestcv:

```
uv tool install https://github.com/kuliberdalabs/honestcv-job-match/archive/refs/tags/v0.1.0.zip
honestcv --version
```

This needs no git. uv downloads Python 3.11 or newer if your computer does not have it. Do not use the Python that comes with macOS (3.9); it is too old. If the shell then says `honestcv` is not found, run `uv tool update-shell`, close the window and open a new one.

See it work before you give it any file of your own:

```
honestcv demo
```

The demo copies a fictional sample (Maya Lindqvist, a support operations analyst moving into data analysis) to a temporary folder. It scores eight sample offers against her two CV variants and checks a cover letter with planted problems. It runs offline, needs no keys, and pins the date to the sample date, so the output is the same every time. Then open the report it prints.

Your own setup is two commands. `honestcv init` works without a CV, but `honestcv scan` needs one: pass it with `--cv` now, or add it later with `honestcv add-cv FILE`.

```
honestcv init --cv your-cv.pdf
honestcv scan
```

[START-HERE.md](START-HERE.md) walks through the whole path with the screen you should see after each step. Polish speakers: [README.pl.md](README.pl.md).

## What's here

| Command | What it does |
|---|---|
| `honestcv demo` | Offline run on the bundled sample: scan, report, and the check of a planted cover letter |
| `honestcv init [DIR] [--cv FILE]` | Creates your workspace (default `~/honestcv`) and reads your CV when you pass one |
| `honestcv add-cv FILE` | Adds another CV variant; every scan scores each offer against every CV |
| `honestcv add-offer --paste` | Imports an offer from the clipboard; also a file (`.eml`, `.html`, `.csv`, `.json`, `.txt`), `--text` or `--url` |
| `honestcv ledger draft` / `ledger lint` | Drafts a starting ledger from your CVs; checks the ledger |
| `honestcv scan` | Collects offers, scores them, writes the report and opens it |
| `honestcv tailor N` | Picks your ledger sentences for offer N and checks them |
| `honestcv check FILE` | Checks any CV or cover letter against your ledger; never changes the file |
| `honestcv doctor` | Read-only health check |
| `honestcv schedule` | Prints a ready daily schedule entry for your computer; installs nothing |
| `honestcv sources` | The job APIs honestcv reads, with their terms |

| Document | Contents |
|---|---|
| [docs/scoring.md](docs/scoring.md) | How the match % is computed, generated from the code's own rules |
| [docs/ledger.md](docs/ledger.md) | The ledger schema, the state ladder, every rule code, a commented example |
| [docs/import.md](docs/import.md) | Bringing in offers from boards honestcv does not read: e-mails, saved pages, CSV, JSON, paste |
| [docs/sources.md](docs/sources.md) | The job APIs, their terms, rate limits and attribution |
| [docs/agents.md](docs/agents.md) | JSON output for agents; [AGENTS.md](AGENTS.md) has the rules for an agent that runs honestcv |

## Design decisions

| Decision | Failure it prevents |
|---|---|
| One deterministic scorer. No model ever changes a match percentage. | Several scoring vocabularies, or model prose that restates the rubric, drift apart. |
| The match % is coverage of the offer by your CV against criteria in one YAML file. Ledger backing is shown beside it and does not change the %. | A first run has no ledger, and the % would jump when you add one. |
| A preference you did not state is not scored, and its weight goes to the other criteria. The skills and language lines do not apply when the offer names no skill or language honestcv knows. Any other fact the offer leaves out never counts as a plus: that line scores 0, and [docs/scoring.md](docs/scoring.md) says line by line when it is also listed as unknown. | Unknown counting as a plus, or an empty config giving meaningless numbers. |
| Inside `tailor`, the checker reads the written file back from disk and removes blocked lines. `check` on your own document only reports. | Shipping text that was never checked, or rewriting text a person wrote. |
| No model calls in this version. | A claims check that only works with a model, a running cost, and data sent to a provider. |
| A fixed list of job APIs whose terms allow automated access. Every other host is refused before a request is made; you import those offers by file or paste. | Reading sites whose terms do not allow it. |
| Five runtime dependencies: typer, pydantic, httpx, PyYAML, pypdf. | Install failures on a non-technical user's computer. |
| `src/` is plain Python modules: no plugin system, no generated code, no network code outside `sources/`. | A tool nobody can audit. |
| A source that changes shape is reported as PARTIAL. | A parser that silently returns zero offers looks like "no matches". |

Prior art: [claimledger-studio](https://github.com/mikepatraw/claimledger-studio), an evidence-backed resume-tailoring console with a claim ledger, is the closest existing concept.

## What this does not do

- Apply to jobs, fill in forms, send e-mails or messages, or contact recruiters.
- Log in anywhere, use cookies or browser automation, or read your mailbox. E-mails come in as files you save.
- Read any host outside its list of job APIs, discover company job boards, or follow links.
- Republish or share offers. It is not a hosted service, has no web interface and has one user: you.
- Use a model to score, or give a "hire probability". The % says how much of the offer your CV covers.
- Rewrite your own documents, in place or beside them.
- Produce a PDF or DOCX CV. Output is Markdown.
- Track applications, send reminders or notifications.
- Install a scheduler entry or run in the background. `honestcv schedule` prints an entry you can install yourself; `honestcv scan --every 6h` repeats in the open window until you press Ctrl-C.
- Convert salary currencies, or translate its messages. The CLI is in English; Polish offers and CVs are read.

## What leaves your computer

- Himalayas gets search terms: the skill names honestcv recognised in your CV (from its built-in list, plus any you add as `profile.skills_add`) and the target titles you typed in `honestcv.yml`, or your `search.keywords` if you set them, plus the country you set. No sentence, job title, employer or date that honestcv reads from your CV is sent.
- RemoteOK, We Work Remotely and Arbeitnow get plain requests with no search terms; honestcv filters their offers on your computer. Greenhouse, Lever and Ashby get only the company board names you list.
- Before the first request to a source in a run, honestcv reads that host's robots.txt.
- Nothing else from your CV, ledger or e-mails leaves the computer. No telemetry, no analytics.
- honestcv makes no LLM calls.

honestcv reads public job APIs whose terms allow it and files you provide. The repository contains no proprietary software, no license circumvention, no credential collection and no authentication circumvention. You are responsible for following the terms of the job boards and APIs you use, including any site whose offers you import.

honestcv is for your personal job search. Do not republish its results.

## Works with

macOS, Windows and Linux, with Python 3.11 or newer through uv.

---

[CHANGELOG](CHANGELOG.md) · [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) · MIT licence ([LICENSE](LICENSE)) · [Po polsku](README.pl.md)

Kuliberda Labs · kuliberda.ai
