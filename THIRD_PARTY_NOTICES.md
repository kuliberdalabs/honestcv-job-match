# Third-party notices

honestcv is MIT-licensed ([LICENSE](LICENSE)). It contains no third-party code. It depends on the packages below, which uv installs from the Python Package Index. Versions are the ones in `uv.lock` as of 2026-10-11.

## Runtime dependencies

| Package | Version | Licence | Pulled in by |
|---|---|---|---|
| typer | 0.27.3 | MIT | honestcv. typer 0.27 includes code adapted from Click 8.3.1 (BSD-3-Clause) in `typer/_click` |
| rich | 15.0.0 | MIT | typer |
| shellingham | 1.5.4 | ISC | typer |
| colorama | 0.4.6 | BSD-3-Clause | typer, on Windows only |
| annotated-doc | 0.0.5 | MIT | typer |
| markdown-it-py | 4.2.0 | MIT | rich |
| mdurl | 0.1.2 | MIT | markdown-it-py |
| Pygments | 2.21.0 | BSD-2-Clause | rich |
| pydantic | 2.14.0 | MIT | honestcv |
| pydantic-core | 2.50.0 | MIT | pydantic |
| annotated-types | 0.8.0 | MIT | pydantic |
| typing-extensions | 4.16.0 | PSF-2.0 | pydantic |
| typing-inspection | 0.4.4 | MIT | pydantic |
| httpx | 0.28.1 | BSD-3-Clause | honestcv |
| httpcore | 1.0.9 | BSD-3-Clause | httpx |
| h11 | 0.16.0 | MIT | httpcore |
| anyio | 4.15.1 | MIT | httpx |
| idna | 3.20 | BSD-3-Clause | httpx |
| certifi | 2026.7.22 | MPL-2.0 | httpx |
| PyYAML | 6.0.3 | MIT | honestcv |
| pypdf | 6.20.0 | BSD-3-Clause | honestcv |

Development only (not installed for users): pytest 9.1.1 (MIT), pluggy 1.6.0 (MIT), iniconfig 2.3.1 (MIT), packaging 26.3 (Apache-2.0 OR BSD-2-Clause).

DOCX files are read with the Python standard library (`zipfile`, `xml.etree`).

## Tested with

| What | Version | Date | Result |
|---|---|---|---|
| Python | 3.11.15 | 2026-10-11 | full test suite passes |
| Python | 3.14.7 | 2026-10-11 | full test suite passes |
| macOS | 27.0.1 (arm64) | 2026-10-11 | test suite and demo |
| uv | 0.9.29 | 2026-10-11 | `uv run`, `uvx --from .` |
| typer | 0.27.3 | 2026-10-11 | |
| pydantic | 2.14.0 | 2026-10-11 | |
| pypdf | 6.20.0 | 2026-10-11 | |

Windows and Linux are covered by the CI workflow in `.github/workflows/ci.yml`; its first run is pending.

## Job sources

honestcv reads these public job APIs. Each verdict is a reading of the site's terms and robots.txt on the date shown, not legal advice. The full table, with rate limits, is in [docs/sources.md](docs/sources.md).

| Source | Terms | Verified | Attribution honestcv gives |
|---|---|---|---|
| Himalayas | <https://himalayas.app/api> | 2026-10-10 | Link back to himalayas.app and name Himalayas as the source |
| RemoteOK | <https://remoteok.com/api> | 2026-10-10 | Followed link back to the RemoteOK listing, name RemoteOK, no logo |
| We Work Remotely | <https://weworkremotely.com/remote-job-rss-feed> | 2026-10-10 | Link back to the listing |
| Arbeitnow | <https://www.arbeitnow.com/terms> | 2026-10-10 | Link back to the listing |
| Greenhouse job boards | <https://developers.greenhouse.io/job-board.html> | 2026-10-10 | The company's own careers page |
| Lever postings | <https://github.com/lever/postings-api> | 2026-10-10 | The company's own careers page |
| Ashby job boards | <https://developers.ashbyhq.com/docs/public-job-posting-api> | 2026-10-10 | The company's own careers page |

Greenhouse, Lever and Ashby are read only for the companies you list in `honestcv.yml`.

## Data files

Every file in `src/honestcv/data/` was written by hand for honestcv and is covered by its MIT licence. None is copied from a third-party list, from ESCO, or from a job board's taxonomy.

| File | Contents |
|---|---|
| `skills.yml` | skill names, aliases and reading cues, English and Polish |
| `stopwords.txt` | English and Polish stopwords |
| `levels.yml`, `gates.yml`, `languages.yml` | level words, filter phrases, language names and requirement cues |
| `verbs.yml`, `nonclaims.yml` | word lists for the claims checker |

The sample workspace in `src/honestcv/samples/` is fictional. Any resemblance to real people or companies is accidental.
