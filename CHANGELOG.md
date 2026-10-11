# Changelog

All notable changes to honestcv are listed here.

## [0.1.0] - unreleased

First version.

- `honestcv demo`: an offline run on a fictional sample, pinned to the sample date.
- `honestcv init`, `add-cv` and `add-offer` (file, `--paste`, `--text`, `--url`): a workspace in `~/honestcv`, CVs as PDF, DOCX, Markdown or text, and offers from job-alert e-mails, saved pages, CSV, JSON and pasted text.
- `honestcv scan`: offers from Himalayas, RemoteOK, We Work Remotely, Arbeitnow, and the Greenhouse, Lever and Ashby boards of companies you list; one deterministic score per offer and CV; an HTML and a Markdown report; `out/matches.csv` and `out/latest.json`; `--json`, `--offline` and `--every`.
- `honestcv ledger draft` and `ledger lint`: a claims ledger with a state ladder, evidence types and approved sentences.
- `honestcv check`: a claims check of any document against the ledger, with rule codes, ledger field paths and safe sentences. It never changes the file.
- `honestcv tailor`: highlights for one offer, built only from ledger sentences and checked from disk.
- `honestcv doctor`, `honestcv sources` and `honestcv schedule` (prints a schedule entry; installs nothing).
- No model calls.

Kuliberda Labs
