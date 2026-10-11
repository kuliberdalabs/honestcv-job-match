# The claims ledger

`ledger.yml` in your workspace lists what you did and what proves it. honestcv uses it in two places:

- `honestcv tailor N` builds highlights for an offer only from the sentences you approved in the ledger.
- `honestcv check FILE` reads any CV or cover letter and flags each line that claims more than the ledger backs.

The match % of an offer never depends on the ledger. The report shows ledger backing beside it ("4 of 5 matched requirements backed by ledger claims").

## Start from your CV

```
honestcv ledger draft
honestcv ledger lint
```

`ledger draft` turns every bullet and every job line of your CVs into a claim marked `draft: true`, and also every other line the check reads as a claim: the Skills line (a `kind: skill` claim listing each skill on it, when the line lists only skills honestcv knows; a Skills line with anything else on it, such as a bracket, a year or an unknown tool, is drafted like a sentence), profile sentences and headings with a year. Each unchanged CV then checks against its draft with no BLOCK. Identical lines from several CVs become one claim. A draft claim is your own CV line, not yet backed by evidence:

- it does not count as ledger backing;
- tailoring and the check accept its sentence only word for word, as it stands in your CV (`DRAFT_CLAIM`, a REVIEW);
- any rewording of it is blocked (`DRAFT_REWORDED`).

A freshly drafted ledger gives one WARN per draft claim and no ERROR, so tailoring works on day one. `ledger draft` never touches a ledger that already has claims; use `honestcv ledger draft --out ledger.draft.yml` for a second draft.

To confirm a claim, add the states you can prove with their evidence, write the sentence you want to use under `allowed`, and delete the line `draft: true`. Then run `honestcv ledger lint` again.

## The state ladder

```
built (1) < tested (2) < shipped (3) < used (4) < acknowledged (5) < paid (6)
```

Plus two side tracks: `generated` (a system produced output; it never means sent or used) and `non_cash_value` (value received without payment; never described as pay).

There is no skipping. A sentence whose verb implies `shipped` ("deployed", "launched", "wdrożyłam", "wdrożenie") needs a confirmed `shipped` or higher.

| State | Evidence types lint accepts |
|---|---|
| built | repo, file, url, note |
| tested | test_record |
| shipped | handover_record |
| used | usage_evidence |
| acknowledged | recipient_statement |
| paid | invoice_or_transfer |
| generated | file_list, file |
| non_cash_value | note |

A `ref` that looks like a path must exist inside your workspace (put the files in `evidence/`). A URL must parse; it is never opened. Free text is accepted only for the type `note`.

## Schema

```yaml
schema_version: 1
person:
  name: Maya Lindqvist        # optional
  as_of: 2026-09-01           # the date the ledger was last true
  stale_after_days: 365       # after this, lint warns that the ledger needs a review

rules:                        # optional
  forbidden_phrases: []       # [{pattern: '(?i)\bsenior\b', reason: "seniority not evidenced"}]
  thresholds: {exact: 0.90, match: 0.60, ambiguous: 0.35}
  verbs_extra: {}             # extra verbs per state, e.g. {shipped: ["went live with"]}

claims:
  - id: backlog-dashboard     # a unique slug; never put a private name in it
    kind: delivery            # delivery | artifact | output | ceiling | biography | skill
    summary: Weekly ticket-backlog dashboard for the customer support department
    skills: [sql, looker studio, google sheets]   # the skills this claim is evidence for
    confirmed:                # state -> evidence
      built:   [{type: file,            ref: evidence/backlog/dashboard-export.txt,     date: 2024-02-12}]
      shipped: [{type: handover_record, ref: evidence/backlog/handover-note.txt,        date: 2024-03-04}]
      used:    [{type: usage_evidence,  ref: evidence/backlog/monday-review-agenda.txt, date: 2024-05-06}]
    quantities:               # numbers you may state, and the state each one is true for
      - {name: teams_using, value: 3, unit: team, state: used}
    beneficiaries: {count: 3, kind: support teams}
    authorship: {role: sole, agent_assisted: false, can_defend_end_to_end: true}
    not_confirmed: [acknowledged, faster response times, company-wide rollout]
    scope_words_allowed: []   # e.g. [production] only if you can show it
    outcomes_allowed: []      # outcome phrases your evidence supports, e.g. ["cut the tag list from 41 to 18"]
    required_qualifiers: []   # words every sentence about this claim must keep, e.g. [course capstone]
    allowed:                  # the sentences you approved; tailoring copies them word for word
      - "Built the weekly ticket-backlog dashboard that three support teams use in their Monday reviews."
    review_by: 2027-03-01     # lint warns after this date

  - id: ml-production-ceiling
    kind: ceiling             # a limit: something you have not done
    statement: "No machine-learning model in production as of 2026-09-01."
    forbidden_here:           # regexes no document may match
      - '(?i)\bmlops\b'
    blocks_requirements:      # offers whose requirements match are filtered out of your results
      - '(?i)models? (to|in|into) production'
    allowed:
      - "My machine-learning work so far is course work; none of it runs in production."

  - id: support-career
    kind: biography
    statement: "Customer support agent 2016-2020, support operations analyst 2020-2026"
    facts: {start_year: 2016, end_year: 2026, precision: year}
    allowed:
      - "Customer support and support operations at a parcel company, 2016-2026."
```

Other fields: `private_names` (names that must never appear in a document, such as a client you may not name), `visibility: never` (a claim that is true but must not appear in a CV), `draft` and `draft_source` (written by `ledger draft`).

The full sample is `src/honestcv/samples/en/ledger.yml` (fictional). It lints with zero ERRORs and zero WARNs on its sample date.

`authorship.role` is one of `sole`, `led`, `contributed` or `directed`. With `agent_assisted: true` and `can_defend_end_to_end: false`, any "wrote", "built" or "author of" is blocked: output an agent produced is not your proof until you can defend it.

## Lint

`honestcv ledger lint` fails closed. Any ERROR blocks `tailor`, and makes `check` return BLOCK with one `LEDGER_LINT` flag per error.

ERROR:

- a schema problem, or a duplicate id;
- a confirmed state without evidence of an accepted type, or a path that does not exist;
- a regex that does not compile;
- a `ceiling` claim without `statement`;
- a claim id that contains a word from any claim's `private_names`;
- an `allowed` sentence of a confirmed claim that its own claim would block, or that misses a required qualifier.

WARN:

- `person.as_of` older than `stale_after_days`, or a `review_by` date that has passed;
- a confirmed claim with no `allowed` sentence (tailoring cannot use it);
- every draft claim.

"Lint clean" means zero ERRORs.

## How the check works

The checker reads the file from disk and splits it into lines, bullets and sentences. A Markdown heading is checked like a sentence; comments are skipped. Section titles ("Experience", "Doświadczenie"), greetings and sign-offs never count as claims. In the usual cover-letter openers and closers, only the phrase itself ("I am writing to apply", "I look forward to") and the job it names ("for the Junior Data Analyst position", "na stanowisko Młodszego Analityka") are not a claim; the rest of the sentence is checked like any other text, so "I am writing to apply with a doctorate ..." is a claim. A line is claim-bearing when it has a claim signal: a verb or state word, an authorship, outcome or venue word, a number, a duration, a skill, a language, a degree, certificate or award, an expertise or seniority word, or a citation marker. A word it shares with a claim's summary is not a signal on its own. A line with no signal still counts when it is attributed to a claim by the steps below; such a line is never `UNBACKED`.

A Skills line (a skills label, or three or more skills, and nothing else) is checked skill by skill: each skill on it must be listed in the `skills` of a confirmed claim, or of the draft claim the line repeats word for word. Any other text on the line sends it through the steps below like a sentence.

Each claim-bearing line is matched to one claim:

1. A line equal to an `allowed` sentence (ignoring case, spacing and the final full stop) takes the exact path: only the document-wide rules, the numbers, the required qualifiers and the draft rule run.
2. A line very close to an `allowed` sentence (a similarity of at least `exact`, 0.90) is matched to that claim, and every rule runs.
3. Otherwise the line's content words are compared with each claim. A share of at least `match` (0.60) matches the claim. Between `ambiguous` (0.35) and `match` the line is matched with an `AMBIGUOUS` REVIEW. Below that it is `UNBACKED`.

Negations count: "I have not deployed a model to production" is a REVIEW, not a BLOCK.

The result is BLOCK when any flag is BLOCK, REVIEW when any flag is REVIEW, else PASS. Every flag names the line, the rule, the ledger field it cites, and a safe sentence from the ledger when there is one. `check` never changes your file and writes nothing beside it.

## Rule codes

| Code | Severity | What it means | Ledger field cited |
|---|---|---|---|
| `LEDGER_LINT` | BLOCK | The ledger has an ERROR; nothing else was checked | the path of the lint error |
| `UNBACKED` | BLOCK | No claim supports the line | none |
| `AMBIGUOUS` | REVIEW | The line only partly matches its best claim | `claims[i]` |
| `CITATION_MISMATCH` | BLOCK | A `<!-- c:ID -->` marker names an unknown claim, or one that does not support the line | `claims[i]` or the missing id |
| `UNBACKED_SKILL` | BLOCK | A skill that no claim lists in `skills` (on a Skills line, no confirmed claim; a draft backs only its own line, word for word) | none |
| `STATE_OVERSTATED` | BLOCK | The verb implies a higher state than the claim's highest confirmed state | `claims[i].confirmed` |
| `NOT_CONFIRMED` | BLOCK | The line states something listed in `not_confirmed` | `claims[i].not_confirmed[j]` |
| `QUANTITY_MISMATCH` | BLOCK | A number that is not in the claim, a "+" or "over" the ledger does not exceed, or a number true only for a lower state | `claims[i].quantities[j]` |
| `SCOPE_INFLATION` | BLOCK | A plural beneficiary ("teams", "clients") or a scope word ("production", "company-wide") the claim does not allow | `claims[i].beneficiaries` or `.scope_words_allowed` |
| `DURATION_MISMATCH` | BLOCK | A duration or year range longer than the claim's facts or evidence dates | `claims[i].facts` |
| `FORBIDDEN_PHRASE` | BLOCK | A pattern from `rules.forbidden_phrases` or the claim's `forbidden_here` | `rules.forbidden_phrases[j]` or `claims[i].forbidden_here[j]` |
| `CEILING_BREACH` | BLOCK (REVIEW after a negation) | The line matches a ceiling's `forbidden_here` | `claims[i].forbidden_here[j]` |
| `VISIBILITY` | BLOCK | A private name, or a `visibility: never` claim | `claims[i].visibility` or `.private_names[j]` |
| `AUTHORSHIP` | BLOCK or REVIEW | The verb's authorship class does not fit `authorship.role` (table below) | `claims[i].authorship` |
| `DRAFT_REWORDED` | BLOCK | A draft claim, reworded | `claims[i].draft` |
| `UNBACKED_OUTCOME` | BLOCK | An outcome word ("reduced", "improved") that no `outcomes_allowed` entry supports | `claims[i].outcomes_allowed` |
| `DRAFT_CLAIM` | REVIEW | A draft claim, word for word | `claims[i].draft` |
| `MISSING_QUALIFIER` | REVIEW | A `required_qualifiers` item is missing | `claims[i].required_qualifiers[j]` |
| `STALE` | REVIEW | The claim's `review_by` has passed, or the ledger is older than `stale_after_days` | `claims[i]` |
| `UNKNOWN_ENTITY` | REVIEW | A capitalized name the claim does not mention, such as an employer or product | `claims[i]` |

Authorship, by `authorship.role` (rows) and the kind of verb in the line (columns):

| role | authored (wrote, built, implemented, author of) | led (led, managed, owned) | contributed (contributed, helped, supported) | directed (commissioned, specified, oversaw) |
|---|---|---|---|---|
| sole | ok | ok | ok | ok |
| led | REVIEW | ok | ok | ok |
| contributed | BLOCK | BLOCK | ok | REVIEW |
| directed | BLOCK | REVIEW | REVIEW | ok |

## Example

The demo's planted cover letter, checked against the sample ledger:

```
BLOCK · 4 lines blocked · 0 lines to review · 1 line passed

line 3  "I deployed our churn model to production and it cut churn by 12%."
  CEILING_BREACH     ml-production-ceiling.forbidden_here[0] matches "deployed our churn model to production"
  STATE_OVERSTATED   "deployed" implies shipped; highest confirmed state of churn-capstone is tested
  NOT_CONFIRMED      churn-capstone.not_confirmed[2] "deployed"
  QUANTITY_MISMATCH  "12%" is in none of churn-capstone's quantities, allowed sentences or facts
  SCOPE_INFLATION    "production" is not in churn-capstone.scope_words_allowed
  MISSING_QUALIFIER  "course capstone" is missing (REVIEW)
  UNBACKED_OUTCOME   "cut" claims an outcome; churn-capstone.outcomes_allowed is empty
  safe: "Built a churn prediction model in Python with pandas and scikit-learn as a course capstone."
```

(The ledger field paths printed after each message are left out here.) Line 11 of the letter is an `allowed` sentence word for word, so it passes.
