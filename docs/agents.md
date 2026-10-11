# honestcv for agents

This page is for a coding agent (any CLI agent) that runs honestcv for a person. `AGENTS.md` at the repo root has the rules: what to run, when to ask, and what never to do. This page documents the JSON that the agent reads.

Every JSON output carries a `schema` field. A schema name ends in a version (`.v1`). Fields are only ever added within a version; a removed or renamed field gets a new version.

## The agent loop

1. Run `honestcv scan --json`. Read `status` first. `PARTIAL` or `FAIL` means a source failed: say which one, from `sources[].message`. Never report "no matches" for a run that is not `PASS`.
2. Show the person the ranked offers. The rank order is in the report; in the JSON, an offer is ranked when one of its `results` has `surfaced: true`.
3. For the offers the person picks, run `honestcv tailor N`, where N is the offer number in the latest report.
4. When you write or rewrite a CV or a cover letter yourself, save the file and run `honestcv check FILE --json` before you show it. Remove or rewrite every line with a `BLOCK` flag, using the ledger's allowed sentences (`safe_rewrite`), and run the check again until no `BLOCK` is left. Show `REVIEW` lines to the person with their `message`.
5. After a scheduled run, read `out/latest.json` and tell the person about `new_matches` through a channel you already have. honestcv itself sends nothing.

Exit codes: 0 ok · 1 user error · 2 invalid config or ledger · 3 partial collection · 4 check BLOCK · 5 internal failure.

## `honestcv scan --json`

Prints the whole run, the same data as `out/runs/<run_id>/scan.json`. Paths are not part of it. Main fields:

| Field | Meaning |
|---|---|
| `status` | `PASS`, `PARTIAL` (a source was refused, unreadable or failed) or `FAIL` (every source failed and the inbox was empty) |
| `sources[]` | one entry per source plus `inbox`: `state` is ok, cached, empty, drift, refused, error or skipped |
| `offers[]` | every offer collected, after deduplication; `thin: true` means title and link only |
| `results[]` | one per offer and CV; `gate` is set when the offer was filtered out; `surfaced` and `low_confidence` decide where it is listed |
| `results[].match_pct` | coverage of the offer by the CV, by the person's criteria. It is not a hire probability |
| `results[].backed` | `[matched requirements with ledger backing, matched requirements]` |
| `gate_counts` | filtered offers per gate code |
| `new_offer_ids` | offers no earlier scan saw (45 days of memory) |

```json
{
  "$id": "honestcv.scan.v1",
  "type": "object",
  "required": ["schema", "run_id", "today", "status", "sources", "cvs", "offers", "results", "gate_counts", "new_offer_ids", "config_digest", "ledger_present"],
  "properties": {
    "schema": {"const": "honestcv.scan.v1"},
    "run_id": {"type": "string"},
    "today": {"type": "string"},
    "status": {"enum": ["PASS", "PARTIAL", "FAIL"]},
    "sources": {"type": "array", "items": {
      "type": "object",
      "required": ["id", "state", "http_status", "offers", "message"],
      "properties": {
        "id": {"type": "string"},
        "state": {"enum": ["ok", "cached", "empty", "drift", "refused", "error", "skipped"]},
        "http_status": {"type": ["integer", "null"]},
        "offers": {"type": "integer"},
        "message": {"type": "string"}
      }
    }},
    "cvs": {"type": "array", "items": {"type": "string"}},
    "offers": {"type": "array", "items": {
      "type": "object",
      "required": ["id", "source", "title", "company", "url", "location", "remote", "contract", "skills", "salary", "description", "posted_at", "attribution", "thin", "possible_duplicate_of"],
      "properties": {
        "id": {"type": "string"},
        "source": {"type": "string"},
        "title": {"type": "string"},
        "company": {"type": ["string", "null"]},
        "url": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "remote": {"enum": ["remote", "hybrid", "onsite", null]},
        "contract": {"type": "array", "items": {"type": "string"}},
        "skills": {"type": "array", "items": {"type": "string"}},
        "salary": {"type": ["object", "null"]},
        "description": {"type": "string"},
        "posted_at": {"type": ["string", "null"]},
        "attribution": {"type": "string"},
        "thin": {"type": "boolean"},
        "possible_duplicate_of": {"type": ["string", "null"]}
      }
    }},
    "results": {"type": "array", "items": {
      "type": "object",
      "required": ["offer_id", "cv_id", "gate", "layer", "layer_assumed", "criteria", "penalties", "base", "match_pct", "unknowns", "requirements", "requirements_recognized", "backed", "surfaced", "low_confidence", "band"],
      "properties": {
        "offer_id": {"type": "string"},
        "cv_id": {"type": "string"},
        "gate": {"type": ["object", "null"]},
        "layer": {"enum": ["CORE", "STRETCH", "DOWNSHIFT"]},
        "layer_assumed": {"type": "boolean"},
        "criteria": {"type": "array", "items": {
          "type": "object",
          "required": ["id", "weight", "applicable", "fraction", "points", "note", "quotes"],
          "properties": {
            "id": {"type": "string"},
            "weight": {"type": "integer"},
            "applicable": {"type": "boolean"},
            "fraction": {"type": "number"},
            "points": {"type": "number"},
            "note": {"type": "string"},
            "quotes": {"type": "array", "items": {"type": "string"}}
          }
        }},
        "penalties": {"type": "array", "items": {"type": "object", "required": ["code", "points", "quote"]}},
        "base": {"type": "number"},
        "match_pct": {"type": "integer"},
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "object", "required": ["term", "must", "in_profile", "backed_by", "quote"]}},
        "requirements_recognized": {"type": "integer"},
        "backed": {"type": "array", "items": {"type": "integer"}},
        "surfaced": {"type": "boolean"},
        "low_confidence": {"type": "boolean"},
        "band": {"enum": ["strong", "good", "fair", null]}
      }
    }},
    "gate_counts": {"type": "object"},
    "new_offer_ids": {"type": "array", "items": {"type": "string"}},
    "config_digest": {"type": "string"},
    "ledger_present": {"type": "boolean"}
  }
}
```

## `honestcv check FILE --json`

Prints the result of the claims check. The checked file is never changed, and nothing is written beside it. The exit code is 4 when the verdict is `BLOCK`.

| Field | Meaning |
|---|---|
| `verdict` | `PASS`, `REVIEW` (a person should look at some lines) or `BLOCK` (at least one line claims more than the ledger backs) |
| `flags[]` | one per finding: `line`, `quote`, `rule` (see `docs/ledger.md`), `severity`, `claim_id`, the ledger `field` it cites, `message`, and `safe_rewrite`, an allowed sentence from the ledger or null |
| `units_claim_bearing` | lines that make a claim; lines without a flag among them are the "lines passed" |
| `lint[]` | ledger lint findings; any `ERROR` makes the verdict `BLOCK` with one `LEDGER_LINT` flag each |
| `gaps[]` | with `--offer`: what the offer asks for that the ledger does not back. Never added to a document |

```json
{
  "$id": "honestcv.check.v1",
  "type": "object",
  "required": ["schema", "verdict", "flags", "units_total", "units_claim_bearing", "units_backed", "lint", "gaps", "document_path", "document_sha256"],
  "properties": {
    "schema": {"const": "honestcv.check.v1"},
    "verdict": {"enum": ["PASS", "REVIEW", "BLOCK"]},
    "flags": {"type": "array", "items": {
      "type": "object",
      "required": ["rule", "severity", "line", "quote", "claim_id", "field", "message", "safe_rewrite"],
      "properties": {
        "rule": {"type": "string"},
        "severity": {"enum": ["BLOCK", "REVIEW"]},
        "line": {"type": "integer"},
        "quote": {"type": "string"},
        "claim_id": {"type": ["string", "null"]},
        "field": {"type": ["string", "null"]},
        "message": {"type": "string"},
        "safe_rewrite": {"type": ["string", "null"]}
      }
    }},
    "units_total": {"type": "integer"},
    "units_claim_bearing": {"type": "integer"},
    "units_backed": {"type": "integer"},
    "lint": {"type": "array", "items": {
      "type": "object",
      "required": ["severity", "path", "line", "message"],
      "properties": {
        "severity": {"enum": ["ERROR", "WARN"]},
        "path": {"type": "string"},
        "line": {"type": ["integer", "null"]},
        "message": {"type": "string"}
      }
    }},
    "gaps": {"type": "array", "items": {"type": "string"}},
    "document_path": {"type": "string"},
    "document_sha256": {"type": "string"}
  }
}
```

## `out/latest.json`

Rewritten after every scan, scheduled runs included. Every path in it is relative to the workspace. `new_matches` lists the ranked offers that no earlier scan saw, in rank order.

```json
{
  "$id": "honestcv.latest.v1",
  "type": "object",
  "required": ["schema", "run_id", "today", "status", "report_html", "report_md", "scan_json", "matches_csv", "new_matches"],
  "properties": {
    "schema": {"const": "honestcv.latest.v1"},
    "run_id": {"type": "string"},
    "today": {"type": "string"},
    "status": {"enum": ["PASS", "PARTIAL", "FAIL"]},
    "report_html": {"type": "string"},
    "report_md": {"type": "string"},
    "scan_json": {"type": "string"},
    "matches_csv": {"type": "string"},
    "new_matches": {"type": "array", "items": {
      "type": "object",
      "required": ["match", "layer", "title", "company", "best_cv", "url"],
      "properties": {
        "match": {"type": "integer"},
        "layer": {"enum": ["CORE", "STRETCH", "DOWNSHIFT"]},
        "title": {"type": "string"},
        "company": {"type": ["string", "null"]},
        "best_cv": {"type": "string"},
        "url": {"type": ["string", "null"]}
      }
    }}
  }
}
```

`out/matches.csv` holds the history: one row per ranked offer per run, with the columns `scan_date, run_id, match, layer, title, company, best_cv, source, url, new`. Its delimiter is `output.csv_delimiter` in `honestcv.yml`.

## `honestcv doctor --json`

A read-only health check. The exit code is 1 when any check is `ERROR`, else 0. The first check, `version`, is a header line.

```json
{
  "$id": "honestcv.doctor.v1",
  "type": "object",
  "required": ["schema", "errors", "warnings", "checks"],
  "properties": {
    "schema": {"const": "honestcv.doctor.v1"},
    "errors": {"type": "integer"},
    "warnings": {"type": "integer"},
    "checks": {"type": "array", "items": {
      "type": "object",
      "required": ["name", "level", "message"],
      "properties": {
        "name": {"type": "string"},
        "level": {"enum": ["OK", "WARN", "ERROR", "SKIP", "INFO"]},
        "message": {"type": "string"}
      }
    }}
  }
}
```

## Running it every day

Propose a daily run and install it only after the person says yes. If your own app runs scheduled tasks, you may use that. Otherwise run `honestcv schedule`, show the printed lines, and run them after the yes. Tell the person how to turn it off; `honestcv schedule` prints those lines too. `honestcv schedule` itself writes and installs nothing.
