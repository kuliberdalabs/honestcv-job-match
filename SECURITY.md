# Security

honestcv processes CVs, a claims ledger and job-alert e-mails on your own computer. It makes no model calls and sends no telemetry. It sends network requests only to the job APIs listed in `honestcv sources` and to their robots.txt files. What leaves your computer from your own data: the skill names honestcv recognised in your CV (from its built-in list, plus any you add as `profile.skills_add`) and the target titles you typed in `honestcv.yml`, or your `search.keywords` if you set them, plus the country you set, go to Himalayas as search terms. Nothing else from your CV, ledger or e-mails leaves the computer.

Your workspace (by default `~/honestcv`) holds personal data: your CVs, your ledger, your salary minimum, and the companies you apply to. `honestcv init` writes a `.gitignore` with the single line `*` so none of it is committed by accident. Reports say "Contains data from your CV; keep it private."

## Reporting a problem

Please report security problems privately, not in a public issue.

Use GitHub's private vulnerability reporting: open the Security tab of this repository and choose "Report a vulnerability". Only the maintainer sees the report.

Include the honestcv version (`honestcv --version`), your operating system, and the steps that show the problem. Do not include your own CV, ledger or e-mails; reproduce the problem with the fictional sample (`honestcv demo`) if you can. Output from `--debug` may contain lines from your CV or ledger; remove them before sending.

## Supported versions

Only the latest release receives fixes.
