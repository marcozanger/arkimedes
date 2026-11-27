# Checks C1-C6: how to run them

Manual checks for `../2026-11-27-workflow-wrapper-design.md`. The build does not start until each
has a result. These scripts are throwaway stand-ins, not part of Arkimedes.

## Setup, once

1. `brew install tmux` (not installed on 2026-11-27).
2. From this folder: `./setup.sh`. It creates a sandbox repo in `~/ark-checks/sandbox`
   (set `ARK_CHECKS_HOME` to use another folder).
3. `cd ~/ark-checks/sandbox && claude`, accept the trust question, then `/exit`.

Your user settings and hooks apply to these sessions, as they would to Ark's.

## Starting a session

`./start.sh <check> [--allow] [--telemetry]` does what Ark would do: a new worktree in the sandbox,
a per-session settings file, and a detached tmux session running `claude` with `--session-id`,
`--effort ultracode`, `--permission-mode auto` and the check's prompt. It prints the tmux name.

- Watch without touching: `tmux capture-pane -p -t <name>`
- Attach: `tmux attach -t <name>` (detach with `Ctrl-b d`)
- Close: `tmux kill-session -t <name>`
- What the stand-in recorded: `./fake_ark.py log`

The docs describe a one-time auto mode notice, and one consent question for the first workflow that
`ultracode` should skip. If anything asks for a key, write down the exact text. Then run the check
again: the second run is the one that counts.

Run in this order. C1, C3 and C6 shape the core: if one fails and its fallback fails too, stop.

## C1 - a workflow starts with no keypress

- Run: `./start.sh c1`, twice. Do not attach until it finishes.
- Pass: `./fake_ark.py log` shows `c1-done` for both sessions, and `/workflows` (attach afterwards)
  lists a run. A `c1-done` without a workflow run is a fail.
- Fallback: `./start.sh c1 --allow` (adds a `Workflow` allow rule). If that fails, try a permission
  hook; the last resort is one manual confirmation per plan.

## C3 - a background wait wakes the session

- Run: `./start.sh c3`. Wait until the log shows `wait started` and the pane is idle.
- Send: `./fake_ark.py send c3 "blue"`
- Pass: with no keypress, the session reacts and the log shows `c3-got blue`.
- Repeat with a long wait: send the message after at least 4 hours, best overnight, once with the
  Mac asleep in between. Waking a session this way is not documented, so test it more than once.
- Fallback: `tmux send-keys -t <name> "blue" Enter`; then `claude --resume <session-id> "blue"`
  (session ids are in `~/ark-checks/sessions.log`). Note which ones work.

## C6 - commit, push and PR with no help

- Needs a GitHub remote. This creates a **private** repo on your account:
  `cd ~/ark-checks/sandbox && gh repo create ark-checks-sandbox --private --source . --push`
- Run: `./start.sh c6`, twice.
- Pass: each session opens a PR with no prompt, and the log shows `c6-pr <url>`. The session is
  also asked to write into the protected state folder with the Write tool: that must be **denied**
  (log shows `c6-guard denied`, and `~/ark-checks/state/protected/` stays empty).
- Fallback: `./start.sh c6 --allow` (allows commit, push and `gh pr create`).
- Note: the deny rule covers the file tools. It does not by itself stop a shell redirect.

## C2 - workflow agents report progress

- Run: `./start.sh c2`, at least twice.
- Pass: for each session the log has `start` and `done` for all six tasks T1-T6, 12 lines, none
  missing or invented. If a prompt blocks them, try `--allow` (allows the stand-in command).
- Fallback: progress per phase from telemetry, or no progress in v1.

## C4 - after an answer, only what was left runs again

Run it together with a second C3 if you like.

- Run: `./start.sh c4`. T3 needs a value only you know. Wait for `ask` and `wait started`.
- Send: `./fake_ark.py send c4 "green"`
- Pass: the log shows one `start` for T1 and one for T2, and T3 finishes with `green`.
- Fallback: accept the repeat cost, or tell the session to skip tasks already committed.

## C5 - cost per plan from telemetry

- In another terminal: `./otlp_sink.py` (leave it running).
- Run: `./start.sh c2 --telemetry`, twice.
- Then: `./otlp_totals.py`
- Pass: each session id from `sessions.log` has its own totals, rows with a workflow run id appear,
  and a session's total is close to what `/usage` shows inside it.
- Fallback: what the status line or transcript gives; else no cost in v1.
- If nothing arrives, check whether your user settings already set `OTEL_*` variables.

## Results

| Check | Result (pass / fallback / fail) | What you saw |
|---|---|---|
| C1 | | |
| C3 short wait | | |
| C3 long wait | | |
| C6 | | |
| C2 | | |
| C4 | | |
| C5 | | |

## Cleanup

Close the tmux sessions, delete `~/ark-checks`, and delete the sandbox repo on GitHub if you made
it (`gh repo delete ark-checks-sandbox`).
