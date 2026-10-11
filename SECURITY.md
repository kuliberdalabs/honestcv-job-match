# Security

honestcv processes CVs, a claims ledger and job-alert e-mails on your own computer. It makes no model calls and sends no telemetry. It sends network requests only to the job APIs listed in `honestcv sources` and to their robots.txt files. The only data from your CV that leaves your computer are the search keywords sent to Himalayas (your target titles and top skills, or the `search.keywords` you set) and the country you set.

Your workspace (by default `~/honestcv`) holds personal data: your CVs, your ledger, your salary minimum, and the companies you apply to. `honestcv init` writes a `.gitignore` with the single line `*` so none of it is committed by accident. Reports say "Contains data from your CV; keep it private."

## Reporting a problem

Please report security problems privately, not in a public issue.

Contact: the maintainer adds a reporting address on kuliberda.ai here before the first release.

Include the honestcv version (`honestcv --version`), your operating system, and the steps that show the problem. Do not include your own CV, ledger or e-mails; reproduce the problem with the fictional sample (`honestcv demo`) if you can. Output from `--debug` may contain lines from your CV or ledger; remove them before sending.

## Supported versions

Only the latest release receives fixes.
