# Sources

honestcv reads the public job APIs in the table below, and the files you give it. Any other address is refused before a request is made.

If a job board you use is not in the table, save its job-alert e-mails as `.eml` files in `offers/inbox/`, save a page with Ctrl+S / Cmd+S, or copy the text of an offer and run `honestcv add-offer --paste`. See [import.md](import.md).

This table is generated from `src/honestcv/sources/registry.yml`. To change it, change the registry. The same table is printed by `honestcv sources`.

| Source | Default | Verdict | Terms | Verified | Min interval | Cache | Attribution |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `himalayas` | on | SHIP_DEFAULT | <https://himalayas.app/api> | 2026-10-10 | 2 s | 6 h | Link back to himalayas.app and name Himalayas as the source. |
| `remoteok` | on | SHIP_DEFAULT | <https://remoteok.com/api> | 2026-10-10 | 1 s | 1 h | Followed link back to the RemoteOK listing, name RemoteOK, no logo. |
| `weworkremotely` | on | SHIP_DEFAULT | <https://weworkremotely.com/remote-job-rss-feed> | 2026-10-10 | 2 s | 1 h | Link back to the listing. |
| `arbeitnow` | on | SHIP_DEFAULT | <https://www.arbeitnow.com/terms> | 2026-10-10 | 2 s | 1 h | Link back to the listing. |
| `greenhouse` | off until companies are listed | SHIP_DEFAULT (user list only) | <https://developers.greenhouse.io/job-board.html> | 2026-10-10 | 1 s | 6 h | The company's own careers page. |
| `lever` | off until companies are listed | SHIP_DEFAULT (user list only) | <https://github.com/lever/postings-api> | 2026-10-10 | 1 s | 6 h | The company's own careers page. |
| `ashby` | off until companies are listed | SHIP_DEFAULT (user list only) | <https://developers.ashbyhq.com/docs/public-job-posting-api> | 2026-10-10 | 1 s | 6 h | The company's own careers page. |

How honestcv behaves toward every source:

- It sends the User-Agent `honestcv/0.1 (+https://github.com/kuliberdalabs/honestcv-job-match)`.
- It reads the host's `robots.txt` before the first request of a run and keeps the answer for 24 hours. A Disallow that covers the path makes the source `refused`.
- It waits at least the minimum interval between two requests to the same source, and keeps answers for the cache time shown. Inside that time it makes no request.
- It retries HTTP 429, 500, 502, 503 and 504 up to 3 times (2, 4 and 8 seconds, or the `Retry-After` value up to 60 seconds). It never retries 401, 403 or 404, and it does not work around them.
- It reports a source that answers with data it cannot read as `drift`, and the run as PARTIAL. It never reports that as "no matches".
- It keeps the good offers of a source that sent some records it cannot read, reports that source as `error` with the number of records skipped, and the run as PARTIAL. The keyword filter keeps that note, even when no offer matches.
- It never logs in, never posts anything and never calls an apply endpoint.

Every offer card names its source and links to the original listing. The offers are for your own job search. Do not republish them.

Verdicts are a reading of each site's terms and robots.txt on the date shown. They are not legal advice.
