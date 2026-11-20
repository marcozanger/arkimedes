# Controlling a run

`ark-loop` is the only writer of a run's state. Commands post a file to `inbox/<repo>/<slug>/`, and the loop applies it
on its next cycle. Runner processes (`ARK_ROLE`) cannot post: desk-guard denies them the folder and the commands refuse
them.

## Commands

| Command | What |
|---|---|
| `ark ps` | per live run a header line (status, elapsed, eta, as in `ark status`), then its open jobs: status, tier, time, tokens, cost, last message; and the open questions |
| `ark logs <job> [-f] [--kind worker]` | a process's log, readable; the archived one after it ends |
| `ark stop <job> [--why "..."]` | with `<repo>/<slug>` instead of a job it stops every open job of that run as one decision, with the same `--why`. For one job: stops its processes, and the job ends blocked; with no process, it blocks on the next cycle. `--why` puts one line after the reason (`stopped by Marco: <why>`), so history and `ark stats` say why |
| `ark retry <job>` | puts a blocked job back on the step where it stopped (see `ark retry` below). With `<repo>/<slug>` it retries every blocked job that blocks on its own; the ones that only waited on them follow. An `aborted` run is refused, the loop never steps it |
| `ark abort <repo>/<slug> [--why "<line>"]` | ends a blocked or waiting plan for good (see `ark abort` below) |
| `ark steer <job> "<instruction>"` | stops the worker and resumes its session with the instruction |
| `ark attach <job>` | hands the worker to you in an interactive session; on a clean exit it goes on to the gate |
| `ark sweep [--dry-run]` | clears what finished and abandoned runs leave behind (see Sweep below); `--dry-run` only shows it |
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
form `<repo>/<slug>:<job>`. A closed run reopens only if its area does not overlap a running or held run of the
same repo: `ark retry` refuses and names the other run and the globs they share, and a retry that reaches the loop
anyway is dropped with an alert.

## ark abort

`ark abort <repo>/<slug>` is Marco saying a blocked (or never started) plan will not be done. It refuses a plan
with live jobs (stop them first) and any runner process. On the next cycle the run becomes `aborted`: the loop
never steps it again, `ark retry` cannot reopen it, its history row says `aborted`, and the control center shows
it in History only, not on the home screen. Home shows only what is in progress (running, held, approved and
waiting) and the drafts: every finished plan (`done`, `blocked`, `aborted`) is in History only.

A run that closed `blocked` with a final PR is looked at every ten minutes: once Marco merges that PR the run
becomes `done`, whatever jobs were left blocked stay on record in its state.

## Sweep

`ark-loop` clears, once a day, what finished runs leave behind; `ark sweep` does it by hand and
`ark sweep --dry-run` shows what would go and why, changing nothing (disk, origin and PRs stay as they are).
`ark sweep` without `--dry-run` refuses any runner process (`ARK_ROLE` set); `--dry-run` is allowed anywhere.

What is swept, per run: its worktrees under the worktrees folder, its `ark/*` branches (local and on origin), its
open PRs (closed with a comment first), and the empty folders left over. Folders under the worktrees folder that
no run owns are swept as orphans.

A run is swept when it is `aborted`, `done` with every job merged, or abandoned: closed `blocked` (or `done` with
a job not merged) for `sweep.abandon_days` days (14) without an `ark retry`. A live run, a run still inside that
window and anything an `ark retry` could need are never touched.

Held items stay and are listed with the reason, for example `uncommitted changes` or commits that exist nowhere
else. Each run prints one block:

```
demo/notes-edit · aborted
  worktree  wt/demo/notes-edit/a  (12 KB)
  branch    ark/notes-edit-a  (local, origin)
  pr        local://demo/pull/1  (close)
  held      wt/demo/notes-edit/b: uncommitted changes
would free 12 KB · 1 worktrees · 1 branches · 1 PRs · 1 held
```

A real sweep adds `removed` or `error: <why>` to each line and ends with `freed …`. It exits 1 when any item had
an error (the rest still go on); with nothing to do it prints `Nothing to sweep.` and exits 0. Problems reading a
run or a repo are warnings on stderr and that run is skipped.

Every deletion is a line in `history/sweeps.jsonl`: `at`, `repo`, `slug`, `why`, `kind`, `target`.

The `sweep` block of `workspace.json`:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `false` turns the daily sweep off; `ark sweep` still works by hand |
| `abandon_days` | `14` | days a blocked run waits for a retry before it counts as abandoned |

## Questions

| Kind | Asked when | Answers |
|---|---|---|
| worker | Euklides asks (see below) | `ok` (or `yes`, or `si` with or without its accent) runs the line `Answer ok to: …`, the runner action Euklides named in `then`; any other text goes to the worker as its instruction; `:stop` blocks the job |
| plan_changed | the plan changed after its approval and the run is held | `stop` ends the run; restoring or re-approving the plan closes it |
| cap_run | the run spent more than the sum of its budgets and raises, and Euklides could not raise it within his margin | `raise <usd>`, or `stop` to end the run |
| cap_day | an account reached its daily cap (one per account and day) | `raise <usd>` adds to today's cap; `stop` waits until tomorrow |

`questions.md` at the Arkimedes root lists them. It is rewritten by the loop and never read back.

## Base refresh

The run's base `ark/<slug>` follows the default branch. Every time origin's default branch moves (looked at every
few minutes; several moves give one refresh, to the newest), the runner checks main into the base, runs the plan's
integration gate on the result, and moves the base only when it is green. Nothing happens while main stays put.

- A textual clash goes to a refresh worker, paid from the plan's `merge` budget (spent across all refreshes of the
  run) or, when the plan has none, `limits.refresh_usd` in the workspace settings (default 3, per refresh).
- A merge that is textually clean but whose gate is red (main alone is green) is looked at once more, a minute later
  and through the usual gate slot and load brake, before any worker is paid. Green the second time: the refresh lands
  and the gate is recorded as flaky (decisions, report, coach queue). Red again: it goes to the worker as above. A
  textual conflict is deterministic and never gets the second look.
- A red main (the gate fails on main itself) never moves the base and is not a job's failure: Marco hears it once,
  `ark ps` shows `main red at <sha8>`, and the jobs wait with `waiting: main is red` until main moves again.
- Marco also hears a refresh that failed while the base was not green, and a clash the budget cannot cover.

When the budget is spent or the worker fails, the run asks a `refresh` question:

| Kind | Asked when | Answers |
|---|---|---|
| refresh | a clash with main that no refresh worker fixed | `retry` starts one more worker with the whole budget again (the extra spend is Marco's to approve); `skip` leaves the base as it is until main moves again |

`ark ps` shows `base: main@<sha8>` (the newest landed refresh, or the base's start), `main red at <sha8>` and
`refresh <n>: <status>` while one is live. The run report has a Base refreshes section.

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
worker one tier up) after a failure, `answer` to a question, `unblock` after a fixable block, `raise` when the job is
at or over its budget and margin is left (see "Raising budgets"), and always `ask`. A job over its budget (plus what
was raised) cannot `retry`, `escalate` or `unblock`; with 4 decisions already taken without Marco it can only `ask`. An `answer` whose mistake would
reach beyond the job (`undo: plan`) is asked instead, with that answer as the proposal. An answer Euklides cannot
give in the agreed shape also asks.

`ask` becomes a worker question holding the situation, Euklides' note and its proposal, and it names in `then` the one
runner action Marco's `ok` carries out: `go_on` (the worker gets the proposal as its instruction; a proposal of
`:stop …` blocks the job), `escalate` (one tier up; at T2 it goes on there, and an event says so), `widen` (with
`files`: they join the job's area; after an area block the head is pushed again, otherwise the worker goes on),
`fix_base` (a base fix with Marco's authority: no `MAX_FIX_BASE` limit, not counted, the job waits on it), `raise`
(with `usd`: added to `usd_raised` and `marco_raised`), `accept` (with `gate`, a shell command: the job's committed
head is accepted on that replacement gate, see below) or `stop` (the job ends blocked with Euklides' note). An `ask`
without a valid `then` is an unusable answer. The runner writes the action at the end of the question, `Answer ok
to: …`, followed by `Other answers: :stop blocks the job; any other text goes to the worker as its instruction.`
`ok`, `yes` and `si` (with or without its accent; any case, trimmed) are the ok words; `commit yes` is text. A `widen` naming a file inside
another job's area cannot be granted: the question says why instead of `Answer ok to`, and `ark answer` refuses an
ok word with that reason. A way out the runner cannot carry out (a PR to main, a plan edit, a push by hand) is never
a `then`: Euklides writes it in the note as what Marco must do himself. A question saved before `then` existed
still works: its `grant` widens and its `raise_usd` raises on `ok`; with neither, `ok` takes the proposal.
`_widen` over the small-fix rule asks with `then: widen`, `_raise` past the margin with `then: raise`, a `fixed`
that fails its check with `then: stop`, and a failed base fix with `then: fix_base`.

**`accept`.** For a head that is already committed and a replacement gate that is a narrower form of the job's own
gate (a `--changed` run), never to skip a red test. Marco's `ok` queues the head for push and gate with no worker: the
job's `accept_gate` replaces the plan's gate for that one run of the gate, run by the runner under the same
environment as any job gate (no new privilege), charged to nothing. The question shows the gate verbatim, so Marco
sees what runs. Green records the gate result (with `accepted_gate`) and the job goes on to PR, review and
integration as a green job; the integration gate is still the plan's. Red asks again with `then: stop` and the
failing tail; a worker never starts. An `accept` without a non-empty string `gate` of at most 500 characters is
unusable like an `ask` without a valid `then`. Without a clean worktree at a committed head the `ok` is not carried
out (next paragraph).

**The effect of every `ok` is checked.** After `_carry_out`, `_effect_ok` looks for what the `then` must have left:
`widen` the files in `run.area`, `raise` a larger `usd_raised`, `fix_base` a live base fix with the job waiting on it,
`escalate` a changed tier (or one already at the top), `accept` the head queued with its gate. When the effect is not
there the runner restores the job, writes an alert `Marco's ok to <then> was not carried out: <why>` and asks a new
question (`then: stop`) saying the way out is a plan edit and a new approval, or `:stop`. It never resumes a worker
on an answer it could not carry out. When a worker does resume on an answer (`_go_on`) and the job's spend is at or
over its budget plus what was raised, a one-time alert names the spend and the budget (once per budget); the worker is
not blocked, since Marco may have chosen to go on.

Every decision appends one line to `history/euklides/decisions.jsonl` (`valid: false` when the answer was unusable),
and Marco's answer to a question it asked is written on that line. Euklides reads `history/euklides/lessons.md` on
every call. A journal that cannot be written is an event; the decision still applies.

A run saved before Euklides, with a job in `needs_judge` or `judging`, loads as `needs_euklides`.

### Euklides settings

The `euklides` block of `workspace.json` sets how Euklides is called. Every key is optional; a missing block or key
keeps its default, and each key overrides only itself.

```json
"euklides": {"model": "opus", "usd_per_call": 3, "minutes_per_call": 20,
             "tools": ["Read", "Grep", "Glob"],
             "denied_tools": ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent"],
             "budget_margin": 0.10, "decisions_per_job": 4}
```

`model` is `haiku`, `sonnet`, `opus` or a `claude-…` id; `usd_per_call` and `minutes_per_call` are positive numbers;
`tools` and `denied_tools` are lists of non-empty strings with no tool in both; `budget_margin` is a number from 0 to
1 (the share of the plan Euklides may raise, see "Raising budgets"); `decisions_per_job` is an integer of 0 or more. A bad value or an unknown key raises a
`ValueError` naming `euklides.<key>` and stops the cycle before any process starts.

Not settings: the actions the runner opens (`allowed`), the 3 base fixes per run, the small-fix rule and the guards that
protect main.

### Raising budgets

Euklides may raise a job's budget, and a run's total, by up to `budget_margin` (10% by default) of what was planned.
The margin is a **total across his decisions**, not per decision: for a job, `budget_margin × its budget` minus what he
already raised on it; for the run, `budget_margin × the planned total` minus `euklides_raised`. A question to Marco is
the only way past it.

- **A job.** `raise` (with `"usd": <positive number>`) opens when the job's spend is at or over its budget plus
  `usd_raised`. Within the margin left it adds to the job's `usd_raised`, the decision is journaled with `raise_usd`,
  and the job goes on with Euklides' note. Past it, it becomes a worker question with the raise as the proposal;
  Marco's `ok` adds it to `usd_raised` (and to `marco_raised`, which never uses Euklides' margin). Every place that
  compares a job's spend with its budget uses `budget + usd_raised`.
- **A run.** When a run spends more than `planned + usd_raised + euklides_raised` and margin is left (and fewer than
  `decisions_per_job` run-level raises were made), the run holds and one Euklides call starts first: process
  `_run.euklides` in the run folder, cwd the ticks folder, read-only tools, no worktree, open replies `raise` and
  `ask`. Its prompt shows spent, the planned total, Marco's raises, Euklides' raises and the margin left. A `raise`
  within the margin adds to `st.euklides_raised` and the run goes on; a raise past it, an `ask`, or no usable answer
  opens the `cap_run` question with Euklides' note and proposal. With no margin left the question opens directly.
  Marco's `raise <usd>` adds to `st.usd_raised`, never to `euklides_raised`. Run-level decisions are journaled with
  job `_run`, and `ark review` lists them with the run's outcome.

### What Euklides can fix

Two more actions let Euklides fix small things himself, inside the **small-fix rule** the runner checks: at most 3
files and 30 changed lines, no binary file, and no file inside another job's area.

- `widen` opens after an area block (files changed outside the area), under budget. It names the files; when the
  job changed exactly those outside its area and the change is within the rule, they become **grants**: the run
  state keeps them under `grants.<job>`, and every later area check (the worker's, the reviewer's, ark-merge's)
  counts them as part of the area. Briefs show them as "Granted in this run". The runner also writes a job's grants
  to `<job>.grants.json` in the run folder (at every grant and at worker spawn) and passes its path as
  `ARK_GRANTS_FILE`: area-guard reads it on every write, so a grant reaches a worker that is already running. A
  missing, unreadable or malformed file means no grants (it can only narrow the area).
- **Companion files** (`lib/ark/companions.py`: `tests/**`, `test/**`, `**/__tests__/**`, `*.test.*`, `*.spec.*`,
  `conftest.py`, `docs/decisions/**`, `docs/*.md`) may be written outside the area without a question. area-guard
  lets a worker write them; after the worker's DONE (and Euklides' `fixed`) the runner counts the companions outside
  the area, and when they are within the small-fix rule together and none lies in another job's area it records
  them as grants (`runner (companion files)`) and an event, with no Euklides call. Otherwise the usual block, and
  Euklides' widen or ask, applies. Source and config files are never companions. A job opts out with
  `companions: false` in its plan entry (default true).
- `fix_base` opens after the integration gate failed twice and is red on the base too, at most 3 times per run. A
  small worker (job name `_fix-<n>`, branch `ark/<slug>-_fix-<n>`, never pushed) fixes `ark/<slug>`; its change must
  be within the rule (files of merged jobs do not count) and the integration gate must pass on it. Then one
  `fix(base): … (Euklides fix <n>)` commit lands on `ark/<slug>` under the run's merge lock, and every job waiting
  on it (`waiting_fix`, `ark ps` shows "waiting for base fix <n>") integrates again. Only one fix runs at a time;
  a job that hits a red base meanwhile waits on the same fix.
- `regate` opens on a red job gate or integration gate whose head already passed a gate or a CLEAN review (the job
  keeps its last 10 such heads in `green_heads`), once per head and gate (`regated`), while decisions remain; it
  ignores the budget, because it costs no model and no money. Euklides picks it when the red tests look like load
  (timeouts, timing, file watchers), and his `note` names them. The job returns to its gate with a `regate` flag:
  while any job carries one, across all runs, no new gate (job, integration, refresh or base fix) and no new worker
  starts, and the re-run itself starts only when no gate of any kind is running. The same command runs again on the
  same head. Green: the job goes on as after any green gate, and the run records the gate in `flaky` (job, kind,
  head, note, the first red tail). Red, a head that moved, a start that fails, or no free machine before the gate's
  own deadline (the job's minutes; for the integration gate, its timeout): Marco gets a question with both red
  outputs, and the flag is dropped. A job that leaves its gate another way (blocked, sent to Euklides) drops the flag
  too, so the machine never stays quiet.

Over the rule, or when the fix fails (its worker, its gate, or a step failing five times), every job concerned
becomes a question to Marco with Euklides' proposal and the reason. For a widen over the rule, `ok` grants the
files; for a failed fix, `ok` starts another one. The fix never syncs `main` into the run, never edits the plan and opens no PR of its own.

The decisions file written at close (`runs/<repo>/<slug>/decisions.md`) is reviewed by Marco in the `ark ui` Decisions tab
or with `ark review <repo>/<slug>`; see `docs/review.md`.

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

- each job's `phase`, from the processes it has running: `developing` (worker, Euklides), `testing` (job or
  integration gate) or `reviewing`, with `since` and, for a gate, `usual_min`, the median of that run's finished
  gates. A job with no process running has none.
- `wait`, per plan and per job that is not running: why nothing moves, as `{kind, text}`. The plan's `wait` is
  `machine` (the load brake of `ark-loop`, read from the run's events, with `load`, `limit` and `busy_with`, the other
  runs with a job working) until a later event of the same step; a job's is `machine`, `dependency` (a job in its `after`
  has not merged), `answer` (a question is open) or `queue`. Null when the run is not running.
- `estimate`, per plan and per job: the budget minutes split into `developing`, `testing`, `reviewing` and `other`
  by the shares of the repo's earlier steps (`lib/ark/phases.py`, from `runs/<repo>/*/steps.jsonl`; every repo's when
  the repo has under 60 min of history; null with none). A history, not a budget. Time waiting for the machine or for
  Marco is not in a budget, so it is not in the split.

- per plan, for the Dashboard: `account` (the account its repo spends on), `euklides` (`decisions` Euklides took for the
  plan and how many of them were `asked`, a question for Marco; from `history/euklides/decisions.jsonl`) and, in `cost`,
  `closed_at` and `tokens` (the plan's `ended` and `tokens` in `history/plans.jsonl`; null until the run closes).

Estimate: each job lasts its `budget.minutes` after its `after` jobs. Forecast: real times for what
finished, the budget for what runs and what is left. Delay is forecast end minus estimated end.

## Caps and anomalies

- Per run: the sum of the plan's job budgets, plus Marco's and Euklides' raises. Over it, running workers finish,
  nothing new starts, Euklides decides first while his margin is left, and then a question asks.
- Per day, per account: `accounts[].usd_per_day` for a rule's account, `limits.usd_per_day` for the personal one,
  against `runs/spend.jsonl` (each line carries its `account`). At the cap, only that account's runs wait: running work
  finishes, nothing new starts, and one `cap_day` question per account and day asks (`runs/daycap.json`, ids `c1`, `c2`…;
  `ark ps` shows it). A raise goes to `runs/raises.jsonl` and counts for today only. A new day, or a higher configured
  cap, closes the question. An unreadable ledger counts as over.
- A worker that is over its tier's minutes, silent for 20 minutes, or over its tier's tokens gets an intervene tick,
  which chooses wait (once), steer (twice per job), stop and escalate, or stop and block. The process's hard deadline
  is twice its tier's minutes.

## Gate limits

`limits` in `workspace.json` (all optional) also keep gates from overloading the machine:

| Limit | Default | What |
|---|---|---|
| `heavy_gates` | 1 | gates of `heavy: true` jobs that run at once |
| `gates_total` | 2 | gates (job, integration, refresh and base fix gates) that run at once, across all runs; 1 or more |
| `gate_workers` | 4 | test processes a gate may spawn |
| `load_max` | cores x 1.25 (min 2) | 1-minute load average above which a gate waits; `0` turns the brake off |
| `load_wait_max` | 900 | seconds a gate waits for the load before it starts anyway |

- Integration gates and base fix gates always take a `heavy_gates` slot, whatever the job's `heavy` says.
- Every gate (job, integration, refresh and base-fix gates) runs with `ARK_TEST_WORKERS=<gate_workers>`, so a plan's
  gate can say `vitest --maxWorkers=$ARK_TEST_WORKERS`. It also sets `VITEST_MAX_WORKERS` and
  `PYTEST_XDIST_AUTO_NUM_WORKERS` (what `-n auto` means) when the environment does not already have them. Jest has no
  such variable. A flag in the gate's own command line wins.
- The load brake applies to gates only, never to workers, reviewers or ticks. A gate that finds the machine busier
  than `load_max` stays in `needs_gate` / `needs_integrate` and is tried again next cycle,
  with one run event when the wait begins. After `load_wait_max` seconds (monotonic, a sleep does not count) it starts
  anyway, with one more event. `heavy` still serialises gates as before; the brake is checked first and takes no heavy slot.

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

## Plan versions

Editing a plan after approval never touches a running plan: the runner runs the frozen version.
`ark replan`, `ark diff` and how a new version is adopted are in `docs/plan-versions.md`.

## Smoke on a real claude

Run these by hand after the merge; the suite uses a fake claude:

1. `onFailure` and the settings env reach the worker.
2. A SIGKILL of claude: does its shell survive?
3. A push to main from inside a worker is denied.
4. `ark attach` resumes the session and sees the work.
5. A `steer` resumes the session and the worker follows it.
6. The notifier reaches the Mac, and the phone once its service is chosen.

- `machine`, at the top of the feed: `load` (the 1, 5 and 15 minute load averages), `cores` and `limit`, the load
  above which a gate waits (`limits.load_max`). The menu shows it under the loop: free, busy (over 70% of the limit) or
  overloaded (over the limit, gates wait).
