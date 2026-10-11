# Changelog

All notable changes to honestcv are listed here.

## [0.1.0] - 2026-10-11

First version.

- `honestcv demo`: an offline run on a fictional sample, pinned to the sample date.
- `honestcv init`, `add-cv` and `add-offer` (file, `--paste`, `--text`, `--url`): a workspace in `~/honestcv`, CVs as PDF, DOCX, Markdown or text, and offers from job-alert e-mails, saved pages, CSV, JSON and pasted text.
- `honestcv scan`: offers from Himalayas, RemoteOK, We Work Remotely, Arbeitnow, and the Greenhouse, Lever and Ashby boards of companies you list; one deterministic score per offer and CV; an HTML and a Markdown report; `out/matches.csv` and `out/latest.json`; `--json`, `--offline` and `--every`.
- `honestcv ledger draft` and `ledger lint`: a claims ledger with a state ladder, evidence types and approved sentences.
- `honestcv check`: a claims check of any document against the ledger, with rule codes, ledger field paths and safe sentences. It never changes the file.
- `honestcv tailor`: highlights for one offer, built only from ledger sentences and checked from disk.
- `honestcv doctor`, `honestcv sources` and `honestcv schedule` (prints a schedule entry; installs nothing).
- No model calls.

Checked and hardened before release:

- Search terms sent to Himalayas are the target titles you type in `honestcv.yml`, then your top 5 skills (with `profile.skills_add`). No line of your CV is sent as a search term.
- A redirect is followed only over https, to a host of the same source and a path its robots.txt allows; any other redirect is reported and that source shows as failed. No cookies are kept or sent.
- A source whose robots.txt cannot be read is reported, and the run is PARTIAL. One unreadable record no longer stops a source. Answers are read up to 15 MB.
- Pasted and text-file offers get their city, country, work mode, contract form and pay from the text, in English and Polish. A paste that is only a link is refused with the next step.
- `honestcv check` ignores ordinary cover-letter openers and closers and section titles, and checks Markdown headings like sentences. Degrees, awards and prizes count as claims.
- `ledger draft` also drafts the Skills line, profile sentences and headings with a year, so an unchanged CV checks against its own draft with no BLOCK. The `DRAFT_CLAIM` message says to confirm the claim in `ledger.yml`.
- Control characters in offer text never reach the terminal, and offer text is escaped in the Markdown files `tailor` writes. `out/latest.json` carries a note that offer text is data, never instructions.
- `add-offer FILE.eml` saves only the offers it finds, not the e-mail. E-mail addresses are removed from offer links.
- `preferences.countries` accepts two-letter codes and country names such as Poland, and refuses anything else. An empty CV is refused in every format. A salary amount such as "Infinity" counts as not stated.
- Offer texts are read up to 100,000 characters, CSV fields up to 2,000,000 characters and JSON files up to 50 levels deep. A damaged inbox file is reported and the other files still import.

Kuliberda Labs
