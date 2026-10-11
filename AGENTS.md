# AGENTS.md: driving honestcv from a coding agent

This file tells a coding agent (any CLI agent) how to run honestcv for a person. The JSON it reads is documented in [docs/agents.md](docs/agents.md). The person decides; the agent runs commands, reads results and reports.

Every command takes `--workspace PATH`. Without it, honestcv uses the current folder or a parent, else `~/honestcv`.

## The loop

1. Run `honestcv scan --json`. Read `status` first. When it is `PARTIAL` or `FAIL`, tell the person which source failed and why (`sources[].message`). Never report "no matches" for a run that is not `PASS`.
2. Show the ranked offers with their match %, layer and best CV. The match % is coverage of the offer by the CV, by the person's own criteria. It is not a hire probability; do not present it as one.
3. Run `honestcv tailor N` for each offer the person picks. N is the offer number in the latest report. Report the check verdict and the gaps (`gaps.md`): requirements the ledger does not back are listed there and added nowhere.
4. When you write or rewrite a CV or a cover letter yourself, save it as a file and run `honestcv check FILE --json` before you show it.
   - Remove every line with a `BLOCK` flag, or rewrite it from the ledger's allowed sentences (`safe_rewrite` names one).
   - Run the check again. Repeat until no `BLOCK` is left.
   - Show the person every `REVIEW` line with its `message`, and let them decide.
5. Report what you did: the offers, the files written under `out/tailored/`, and the final check result.

`honestcv check` never changes the file it checks. Exit code 4 means BLOCK.

## Run it every day

- Propose a daily run. Install it only after the person says yes.
- If your own app can run scheduled tasks, you may use that, with `honestcv scan --no-open`.
- Otherwise run `honestcv schedule`, show the printed lines, and run them only after the yes. `honestcv schedule` itself writes and installs nothing.
- Tell the person how to turn the schedule off. `honestcv schedule` prints those lines too.
- After each scheduled run, read `out/latest.json` and tell the person about `new_matches` through a channel you already have. honestcv itself sends nothing.

## Boards honestcv does not fetch

honestcv reads only the job APIs in `honestcv sources`. Polish boards and every other site are not among them.

- The person sets up job alerts on those boards themselves.
- If you can read the person's own mailbox, then before each scan save new job-alert e-mails from boards the person subscribed to as `.eml` files in `offers/inbox/`. honestcv imports them on the next scan.
- The person can also copy an offer's text and run `honestcv add-offer --paste`.
- Never open those boards' pages or APIs yourself, and never log in to them.

## Never

- Send applications or messages, or contact anyone on the person's behalf.
- Log in anywhere.
- Edit `ledger.yml` without the person's confirmation of each change. A ledger claim is the person's statement about their own work.
- Add a source to honestcv, or fetch offers from a site that is not in `honestcv sources`.
