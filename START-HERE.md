# Start here

From nothing to checked CV highlights in about 30 minutes. Each step shows the screen you should see. The screens come from real runs of honestcv 0.1.0 with the fictional sample CV of Maya Lindqvist; your names, numbers and dates will differ.

## 1. Install (5 minutes, once per computer)

Install uv, a small tool that installs Python programs:

- macOS: press Cmd+Space, type Terminal, press Enter, then run:
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: open Start, type PowerShell, press Enter, then run:
  `winget install --id=astral-sh.uv -e`

Close the window and open a new one. Then:

```
$ uv tool install https://github.com/kuliberdalabs/honestcv-job-match/archive/refs/tags/v0.1.0.zip
$ honestcv --version
honestcv 0.1.0
```

If the shell says `honestcv` is not found, run `uv tool update-shell`, close the window and open a new one. Do not use the Python that comes with macOS; uv brings its own.

## 2. See the demo (2 minutes)

```
$ honestcv demo
honestcv 0.1.0 · demo · offline · no API keys · sample data only · sample date 2026-10-10
Workspace: /private/tmp/honestcv-demo-ohgg0nn8/   (English sample: Maya Lindqvist, fictional)

Read 8 sample offers; scored 4 against 2 CV variants.

  #  Match  Layer      Offer                                                  Best CV
  1   83%   CORE       Data Analyst · Tallgrove Analytics                     maya-analytics.md
  2   68%   STRETCH    Analytics Engineer · Quarry Lane Software              maya-analytics.md
  3   64%   CORE       Support Operations Analyst · Mossby Support Services   maya-support-ops.md
  4   62%   DOWNSHIFT  Junior Reporting Analyst · Example Bank                maya-analytics.md
  Filtered out: 4 (deny-list 1, commission only 1, expired 1, ledger ceiling 1)

Match % = how much of the offer your CV covers, by your own criteria. Not a hire probability.

Claims check of a planted cover letter (letters/cover-letter-planted.md):
  BLOCK · 4 lines blocked · 0 lines to review · 1 line passed
  line 3  "I deployed our churn model to production and it cut churn by 12%."
          CEILING_BREACH, STATE_OVERSTATED, NOT_CONFIRMED, QUANTITY_MISMATCH, SCOPE_INFLATION,
          UNBACKED_OUTCOME, MISSING_QUALIFIER  (churn-capstone, ml-production-ceiling)
  line 5  "Led a team of 4 analysts building the backlog dashboard for all 9 support teams."
          QUANTITY_MISMATCH, SCOPE_INFLATION  (backlog-dashboard)
  line 7  "Over 12 years of customer support experience at a parcel company."
          DURATION_MISMATCH  (support-career)
  line 9  "My tag cleanup reduced handling time by 25% across the company."
          NOT_CONFIRMED, QUANTITY_MISMATCH, SCOPE_INFLATION, UNBACKED_OUTCOME  (tag-cleanup)
Report: /private/tmp/honestcv-demo-ohgg0nn8/out/report-latest.html
Next: honestcv init --cv path/to/your-cv.pdf
```

The report opens in your browser. Each offer card shows why it got its %. The layer says the direction of the role: CORE is your level, STRETCH is a step up, DOWNSHIFT a step down.

## 3. Create your workspace with your CV (2 minutes)

Your CV can be PDF, DOCX, Markdown or plain text. A file name with spaces goes in quotes, or drag the file into the window.

```
$ honestcv init --cv maya-cv.md
Created ~/honestcv/
  honestcv.yml     settings; every line optional, defaults work
  cvs/             your CVs (.pdf .docx .md .txt); maya-cv.md copied here
  ledger.yml       your claims ledger, empty (start with: honestcv ledger draft)
  offers/inbox/    drop job-alert .eml, saved .html, .csv or .json files here
  evidence/        optional files your ledger points to
  out/             reports land here
  .gitignore       contains "*": nothing in this folder is meant for git
Read cvs/maya-cv.md: 10 skills, languages en (C1), sv.
Job titles and dates found under experience headings:
  Support Operations Analyst     2020-2026
  Customer Support Agent         2016-2020
  about 10 years in total (used only for "N+ years" requirements)
Set profile.level in honestcv.yml to get level matching. A guess from CV dates is wrong if you are changing careers.
Next: honestcv scan
```

## 4. Say what you are looking for (5 minutes)

Open `~/honestcv/honestcv.yml` in a text editor. Every line is optional; what you leave empty is not scored. These lines matter most:

```yaml
profile:
  level: mid
  target_titles: ["data analyst", "reporting analyst"]
preferences:
  remote: [remote, hybrid]
  countries: [SE]
  deny_titles: ["machine learning engineer", "platform engineer"]
```

`target_titles` drives the title score and the search terms. `deny_titles` filters out roles you do not want; they are still listed under "Filtered out", never hidden. Add both languages if you search in two, for example `["data analyst", "analityk danych"]`.

## 5. Draft your claims ledger (10 minutes)

```
$ honestcv ledger draft
Drafted 8 claims from 1 CV into ledger.yml. All are marked draft: true.
A draft claim does not count as ledger backing until you confirm it. Tailoring and the check accept it only word for word, as it stands in your CV.
To confirm a claim, add its state (built, tested, shipped, used, ...), its evidence and the sentence you want to use.
Each claim has comments showing what to fill. Then run: honestcv ledger lint

$ honestcv ledger lint
ledger.yml: 8 claims · 0 confirmed · 8 draft
WARN   line 25  support-operations-analyst-brennvik-parcel: draft: your own CV line, not yet backed by evidence
       (7 more draft claims with the same WARN)
No ERROR lines: tailoring and the check can use this ledger.
```

You can go on with draft claims. Confirm them later, one at a time: [docs/ledger.md](docs/ledger.md) explains the states and the evidence each one needs.

## 6. Scan (2 minutes)

```
$ honestcv scan
```

honestcv reads the job APIs in `honestcv sources`, adds the offers in `offers/inbox/`, scores everything and opens the report. Offers from boards it does not read come in by paste, e-mail or saved page ([docs/import.md](docs/import.md)):

```
$ honestcv add-offer sample-offers.json
Copied to offers/inbox/sample-offers.json: 8 offers. They join the next scan.
```

The screen below comes from `honestcv scan --offline` with only that sample file in the inbox, so the job APIs show `skipped`. A normal scan shows `ok` and a count for each of them.

```
$ honestcv scan --offline
Sources   himalayas skipped · remoteok skipped · weworkremotely skipped · arbeitnow skipped · inbox 1 file, 8 offers
Gates     filtered 4: deny-list 2 · commission only 1 · expired 1
Scored    4 offers x 1 CV · surfaced 4 · needs the full text 0 · new since last scan 4
Status    PASS
Report    out/report-latest.html  (also out/report-2026-10-11.md)
Matches   out/matches.csv  +4 rows (one per surfaced offer) · out/latest.json
```

`Status` says PARTIAL and names the source when a job API refused access or sent data honestcv could not read. A failed source is never shown as "no matches".

## 7. Tailor highlights for one offer (2 minutes)

Use the offer number from the report.

```
$ honestcv tailor 1
Offer   Data Analyst · Tallgrove Analytics (81%, CORE, best CV maya-cv.md)
Mode    deterministic: selects and orders your ledger sentences, never rewrites them
Picked  2 claims · 6 required skills have no ledger backing: sql, python, looker studio, excel, airflow, dbt (listed in gaps.md, added nowhere)
Check   REVIEW (read back from disk)
Wrote 2 bullets to paste into your CV
        out/tailored/2026-10-11-tallgrove-analytics-data-analyst/
        highlights.md  highlights.cited.md  check.md  gaps.md
Before you send the final CV or letter, run: honestcv check FILE
```

`highlights.md` is the file to paste from. The skills count as unbacked here because all claims are still drafts; confirmed claims back the skills they list.

## 8. Check what you send (2 minutes)

Run the check on the final CV or cover letter, whoever wrote it:

```
$ honestcv check ~/honestcv/out/tailored/2026-10-11-tallgrove-analytics-data-analyst/highlights.md
REVIEW · 0 lines blocked · 2 lines to review · 0 lines passed

line 3  "Built a churn prediction model in Python with pandas and scikit-learn as a course capstone."
  DRAFT_CLAIM        built-churn-prediction-model-python is a draft: your own CV line, not yet backed by evidence (REVIEW)  [claims[7].draft]
  safe: "Built a churn prediction model in Python with pandas and scikit-learn as a course capstone."
line 4  "Wrote the SQL queries behind the monthly ticket volume report."
  DRAFT_CLAIM        wrote-sql-queries-monthly-ticket is a draft: your own CV line, not yet backed by evidence (REVIEW)  [claims[3].draft]
  safe: "Wrote the SQL queries behind the monthly ticket volume report."

Ledger: 8 warnings. Run: honestcv ledger lint
Your file was not changed. Fix the lines yourself, then run the check again before sending.
```

BLOCK means a line claims more than your ledger backs; fix it before sending. REVIEW asks you to look. `check` never changes your file.

## If something goes wrong

```
$ honestcv doctor
honestcv 0.1.0 · Python 3.11.15 · macOS 27
OK    workspace   ~/honestcv
OK    config      honestcv.yml valid · criteria weights sum to 100
OK    cvs         1 readable (maya-cv.md)
WARN  ledger      8 draft claims; tailoring and the check accept them only word for word
OK    sources     4 enabled · last scan PASS 2026-10-11 02:36
SKIP  network     run honestcv doctor --online to test the sources
0 errors · 1 warning
```

Every error message says what happened, where, and how to fix it. To run a scan every morning, `honestcv schedule` prints a ready entry for your computer and how to remove it again.
