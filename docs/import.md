# Importing offers

honestcv reads only the job APIs listed in [sources.md](sources.md). Offers from any other board, Polish boards included, come in through files you save or text you copy. honestcv never opens those sites itself.

Every way below puts a file into `offers/inbox/` in your workspace. Every scan reads that folder again. honestcv remembers each file by its content, so a file is never moved or deleted, and the same offer is not reported as new twice.

## Paste (the quickest way)

1. Open the offer in your browser and select its text (Ctrl+A / Cmd+A), then copy it (Ctrl+C / Cmd+C).
2. Run:

```
honestcv add-offer --paste
```

The first heading or the first line becomes the title, unless you give `--title "Data Analyst"`. `--company "Example Bank"` sets the company.

The clipboard is read with `pbpaste` on macOS, `Get-Clipboard` in Windows PowerShell, and `wl-paste` or `xclip` on Linux when one of them is installed.

For scripts, `--text` takes the text as an argument, or reads it from standard input when there is no argument:

```
honestcv add-offer --text "Reporting Analyst. Requirements: SQL, Excel, Looker Studio." --company "Example Bank"
```

`--paste`, `--text` and `--url` save the offer as `offers/inbox/added-<date>-<title>.json`, in the JSON format described below.

## Job-alert e-mails (.eml)

Most job boards can send you new offers by e-mail. Save those e-mails as `.eml` files in `offers/inbox/`, or run `honestcv add-offer alert.eml`.

- Gmail: open the message, then the message menu, then Download message.
- Outlook: open the message, then the message menu, then Save as.
- Apple Mail: select the message, then the File menu, then Save As.

Menu names differ between versions and languages. Look for "Download message" or "Save as", and keep the `.eml` file type.

What honestcv takes from an e-mail:

- Offers described as schema.org `JobPosting` data inside the message, when the board includes it.
- Otherwise the job links with their link text. Such an offer has a title and a link only. It is listed under "Needs the full text" and never ranked, because a title alone gives a misleading score. To score it, open the link and import the text with `add-offer --paste`.
- Links whose text is "unsubscribe", "manage alerts", "wypisz", "zrezygnuj" or "ustawienia powiadomień" are dropped.
- A link that goes through the mail service's redirect host is kept as it is and marked "tracking link from your e-mail".

What it never keeps: the sender, the recipients, the headers, or any text that is not part of an offer. E-mail addresses and phone numbers in offer descriptions are removed.

## Saved pages (.html)

In your browser, use Save page as (Ctrl+S / Cmd+S), then run `honestcv add-offer page.html`. honestcv reads the `JobPosting` data of the page first, and the readable text otherwise.

Safari: save as Page Source, not as Web Archive. honestcv cannot read `.webarchive` files.

## Text files (.txt, .md)

One file is one offer. The first heading or line is the title. On a Mac, TextEdit saves rich text by default: choose Format, then Make Plain Text, before you save.

## CSV and JSON

For a list of offers you keep yourself. A CSV file can use a comma, a semicolon or a tab between columns; honestcv picks the one the header line uses.

| Column / key | Required | Meaning |
|---|---|---|
| `title` | yes | Job title |
| `description` | one of the two | The offer text |
| `url` | one of the two | Link to the listing. Tracking parameters (utm_*, fbclid, gclid and similar) are removed |
| `company` | no | Company name |
| `location` | no | City or region, as text |
| `countries` | no | ISO 3166 codes such as `PL` or `SE`; separated by `;` in CSV, a list in JSON |
| `remote` | no | `remote`, `hybrid` or `onsite` (`zdalnie`, `hybrydowo`, `stacjonarnie` work too) |
| `contract` | no | `employment`, `b2b`, `contract`, `freelance` or `internship` |
| `level` | no | `intern`, `junior`, `mid`, `senior` or `lead` |
| `skills` | no | Required skills; separated by `;` in CSV |
| `skills_nice` | no | Nice-to-have skills |
| `languages` | no | Required languages as ISO 639-1 codes, such as `en` or `pl` |
| `salary_min`, `salary_max` | no | Numbers |
| `currency` | no | ISO 4217 code such as `PLN`, `EUR` or `SEK` |
| `salary_unit` | no | `hour`, `day`, `month` or `year` |
| `posted_at`, `valid_through` | no | Dates as `2026-10-10` |
| `requisition_id` | no | The employer's own id for the job; two listings with the same id are merged |

A row without `title`, or without both `description` and `url`, is skipped with a note. Unknown columns are ignored with one note per file. An offer with no description is treated like an alert link: listed, never ranked.

CSV example (`offers.csv`). A cell that holds the column separator, such as a skills list separated by `;` in a `;` file, goes in double quotes:

```
title;company;url;countries;remote;skills;salary_min;salary_max;currency;salary_unit;description
Data Analyst;Example Bank;https://jobs.example.invalid/101;PL;hybrid;"sql;excel;power bi";12000;15000;PLN;month;"We are looking for a data analyst. Requirements: SQL, Excel, Power BI. Fluent English."
```

JSON example (`offers.json`): a list of offer objects, or an object with an `offers` list.

```json
[
  {
    "title": "Data Analyst",
    "company": "Example Bank",
    "url": "https://jobs.example.invalid/101",
    "countries": ["PL"],
    "remote": "hybrid",
    "skills": ["sql", "excel", "power bi"],
    "salary_min": 12000,
    "salary_max": 15000,
    "currency": "PLN",
    "salary_unit": "month",
    "description": "We are looking for a data analyst. Requirements: SQL, Excel, Power BI. Fluent English."
  }
]
```

## A link only (--url)

```
honestcv add-offer --url "https://jobs.example.invalid/offer/77" --title "Data Analyst"
```

honestcv stores the link as an offer with no text. It never opens the page. The offer is listed under "Needs the full text" until you import its text, and honestcv prints how to do that:

> honestcv only fetches from the job APIs listed in `honestcv sources`. Save the page (Ctrl+S / Cmd+S) and run `honestcv add-offer page.html`, or copy the text and run `honestcv add-offer --paste`.

## Whose terms apply

You are responsible for complying with the terms of service of any site whose offers you import. The offers are for your own job search; do not republish them.
