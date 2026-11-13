# Controlling a run

`ark-loop` is the only writer of a run's state. Commands post a file to `inbox/<repo>/<slug>/`, and the loop applies it
on its next cycle. Runner processes (`ARK_ROLE`) cannot post: desk-guard denies them the folder and the commands refuse
them.

## Commands

| Command | What |
|---|---|
| `ark ps` | per live run a header line (status, elapsed, eta, as in `ark status`), then its open jobs: status, tier, time, tokens, cost, last message; and the open questions |
| `ark logs <job> [-f] [--kind worker]` | a process's log, readable; the archived one after it ends |
| `ark stop <job>` | stops its processes, and the job ends blocked; with no process, it blocks on the next cycle |
| `ark retry <job>` | puts a blocked job back on the step where it stopped (see `ark retry` below) |
| `ark steer <job> "<instruction>"` | stops the worker and resumes its session with the instruction |
| `ark attach <job>` | hands the worker to you in an interactive session; on a clean exit it goes on to the gate |
| `ark answer [--list] [--run <repo>/<slug> <id> "<text>"] [--cap <id> "<text>"]` | answers open questions, one by one (account questions first); `--cap` answers an account's daily cap question |

`<job>` also takes the form `<repo>/<slug>:<job>`, needed when two live runs share a job name or the run has finished.

## ark retry

`ark retry <job>` lifts a block by hand. It refuses a job that is not blocked and any runner process
(`ARK_ROLE` set). It posts a `retry` inbox item; on the next cycle the job returns to the step it was blocked
from, with that step's counters cleared:

| Blocked from | Goes back to |
|---|---|
| working, euklides, waiting_answer, attached | needs_worker |
| gating | needs_gate |
| reviewing | needs_review |
| integrating, needs_merge | needs_integrate |
| waiting_dep | pending |
| another `needs_*` step | that step |
| unknown (older code) | needs_push with a head, else needs_worker |

Jobs blocked only because they waited on it go back to pending. A run that closed blocked reopens when a retry
for one of its blocked jobs arrives: it keeps its final PR and closes again normally. A closed run needs the long
form `<repo>/<slug>:<job>`.

## Questions

| Kind | Asked when | Answers |
|---|---|---|
| worker | Euklides asks (see below) | `ok` takes its proposal; any other text is the answer; `:stop` blocks the job |
| plan_changed | the plan changed after its approval and the run is held | `stop` ends the run; restoring or re-approving the plan closes it |
| cap_run | the run spent more than the sum of its budgets | `raise <usd>`, or `stop` to end the run |
| cap_day | an account reached its daily cap (one per account and day) | `raise <usd>` adds to today's cap; `stop` waits until tomorrow |

`questions.md` at the Arkimedes root lists them. It is rewritten by the loop and never read back.

## Euklides

Euklides (Opus, read-only: Read, Grep, Glob, in the job's worktree) decides how a job goes on. The job's status is
`needs_euklides` while it waits for it and `euklides` while it runs. It is called when:

| Trigger | When |
|---|---|
| failure | a worker attempt failed: FAILED, an error, a red job gate, three review rounds without CLEAN |
| question | a worker ended BLOCKED; its question goes to Euklides before Marco |
| blocked | changed files outside the area, a reviewer without verdict twice, an integration gate failed twice |

Only two blocks skip Euklides: Marco's stop (`ark stop`, `:stop`) and ark-merge refusing. Every other block goes
to Euklides first, including the intervene tick's `stop_block`.

A passing fault (an error in a step, an unreachable worker or review model, processes left in the worktree, a
push that is not a fast-forward, an integration check with no result, a result that cannot be archived) heals by
itself: the job returns to the step that failed after 60, 120, 240, 480 and 900 seconds, with no alert. If it still
fails, Euklides decides with the last error (trigger `blocked`); when he lets it go on, the job returns to that
same step and the worker's work stands. An unreachable Euklides keeps backing off and alerts at the second try.

A job whose dependency is blocked, or waits for Marco (`waiting_answer`, `attached`), is `waiting_dep`
(`ark ps` shows "waiting on <job>") and starts again when the dependency moves. When every live job waits on a
blocked one, the run closes and blocks them with the reason.

A run with an open question stays open, and the question is alerted again once a day until it is answered.

The runner, not the model, decides which actions are open: `retry` (same tier, same session) and `escalate` (a fresh
worker one tier up) after a failure, `answer` to a question, `unblock` after a fixable block, and always `ask`. A job
over its budget, or with 4 decisions already taken without Marco, can only `ask`. An `answer` whose mistake would
reach beyond the job (`undo: plan`) is asked instead, with that answer as the proposal. An answer Euklides cannot
give in the agreed shape also asks.

`ask` becomes a worker question holding the situation, Euklides' note and its proposal. `ok` takes the proposal (a
proposal of `:stop …` blocks the job), `:stop` blocks it, and any other text is the answer the job goes on with.

Every decision appends one line to `history/euklides/decisions.jsonl` (`valid: false` when the answer was unusable),
and Marco's answer to a question it asked is written on that line. Euklides reads `history/euklides/lessons.md` on
every call. A journal that cannot be written is an event; the decision still applies.

A run saved before Euklides, with a job in `needs_judge` or `judging`, loads as `needs_euklides`.

### What Euklides can fix

Two more actions let Euklides fix small things himself, inside the **small-fix rule** the runner checks: at most 3
files and 30 changed lines, no binary file, and no file inside another job's area.

- `widen` opens after an area block (files changed outside the area), under budget. It names the files; when the
  job changed exactly those outside its area and the change is within the rule, they become **grants**: the run
  state keeps them under `grants.<job>`, and every later area check (the worker's, the reviewer's, ark-merge's)
  counts them as part of the area. Briefs show them as "Granted in this run".
- `fix_base` opens after the integration gate failed twice and is red on the base too, at most 3 times per run. A
  small worker (job name `_fix-<n>`, branch `ark/<slug>-_fix-<n>`, never pushed) fixes `ark/<slug>`; its change must
  be within the rule (files of merged jobs do not count) and the integration gate must pass on it. Then one
  `fix(base): … (Euklides fix <n>)` commit lands on `ark/<slug>` under the run's merge lock, and every job waiting
  on it (`waiting_fix`, `ark ps` shows "waiting for base fix <n>") integrates again. Only one fix runs at a time;
  a job that hits a red base meanwhile waits on the same fix.

Over the rule, or when the fix fails (its worker, its gate, or a step failing five times), every job concerned
becomes a question to Marco with Euklides' proposal and the reason. For a widen over the rule, `ok` grants the
files. The fix never syncs `main` into the run, never edits the plan and opens no PR of its own.

Every widen and fix_base is journaled in `history/euklides/decisions.jsonl` (with its `files`), so `ark review`
shows them; each sends a notice, and a landed fix shows in the final PR's commit list.

Ending a run stops every process and blocks every open job. No final PR is opened; what merged stays on `ark/<slug>`
and the worktrees stay.

A finished run (`done` or `blocked`) can still hold a plan back: an approved plan whose area overlaps it, in the
same repo, keeps waiting while that run's final PR is open, and starts on the next cycle after the PR is merged or
closed, from a default branch that already holds those changes. Marco hears that wait once, with the PR's URL.

## Notifications

`notify` in `workspace.json` is an argv; the message becomes its last argument. Empty means no notifier. Alerts are
always kept in `runs/alerts.jsonl`, whether or not the notifier works. Critical alerts: a question from a cap, a merge
refused, an unreachable model, a run's end, and "nothing left to advance".

For the phone, for example:

```json
"notify": ["sh", "-c", "curl -fsS -m 8 -d \"$1\" https://ntfy.sh/<your-topic>", "notify"]
```

## The control center feed

`ark runs --json` prints, read-only, everything the control center app shows: the loop heartbeat,
the last 200 alerts and every approved plan with its jobs, times, events, plan file and decisions.
Its `version` field changes when the shape does. It reads:

- `runs/loop.json`, written by `ark-loop` at every poll. The loop counts as alive while the beat is
  younger than three polls, and at least a minute.
- `runs/alerts.jsonl`, one line per alert, with `repo`, `slug` and `job` when the alert belongs to a
  run. An alert's `id` is its line number.
- `started` and `finished` on each job in `state.json`, stamped by the loop once per pass.

Estimate: each job lasts its `budget.minutes` after its `after` jobs. Forecast: real times for what
finished, the budget for what runs and what is left. Delay is forecast end minus estimated end.

## Caps and anomalies

- Per run: the sum of the plan's job budgets. Over it, running workers finish, nothing new starts, and a question asks.
- Per day, per account: `accounts[].usd_per_day` for a rule's account, `limits.usd_per_day` for the personal one,
  against `runs/spend.jsonl` (each line carries its `account`). At the cap, only that account's runs wait: running work
  finishes, nothing new starts, and one `cap_day` question per account and day asks (`runs/daycap.json`, ids `c1`, `c2`…;
  `ark ps` shows it). A raise goes to `runs/raises.jsonl` and counts for today only. A new day, or a higher configured
  cap, closes the question. An unreadable ledger counts as over.
- A worker that is over its tier's minutes, silent for 20 minutes, or over its tier's tokens gets an intervene tick,
  which chooses wait (once), steer (twice per job), stop and escalate, or stop and block. The process's hard deadline
  is twice its tier's minutes.

## New code

Between polls the loop hashes `lib/ark/*.py` and `bin/ark-loop`. When the hash differs from the one it started with,
it runs `python -m ark.loop --self-check` on the code on disk (imports it, reads the workspace and every run, takes
no lock, starts nothing). If that passes, it prints `ark-loop: restarting on new code <old> → <new>`, sends the
notice and re-execs itself in the same process; the cycle already saved every state, workers keep running, and the
new loop adopts them from the state and the `.proc` files with no second attempt. If the self-check fails, the old
loop keeps running and sends one alert for that code version; it tries again only when the code changes again.

## Sleep

At each poll the loop compares the wall clock with `CLOCK_UPTIME_RAW`; a gap over 60 s is a sleep.
Every deadline of a live run moves by it (job limits, `closing_wait`, each `.proc` deadline), with
one event, "the machine slept …". Every process alive at the wake gets one free failure: no
attempt, tier or counter is spent, and a worker resumes its session. Nothing new starts until
`api.github.com` and `api.anthropic.com` answer, for up to 10 minutes (one event if it gives up);
meanwhile the status line is prefixed with a Spanish notice, "waiting for the network after a suspension" (`esperando red tras suspensi\u00f3n` in code, escaped to keep the sources ASCII). A sleep while `ark-loop` is not
running is not seen.

## Smoke on a real claude

Run these by hand after the merge; the suite uses a fake claude:

1. `onFailure` and the settings env reach the worker.
2. A SIGKILL of claude: does its shell survive?
3. A push to main from inside a worker is denied.
4. `ark attach` resumes the session and sees the work.
5. A `steer` resumes the session and the worker follows it.
6. The notifier reaches the Mac, and the phone once its service is chosen.
