# Controlling a run

`ark-loop` is the only writer of a run's state. Commands post a file to `inbox/<repo>/<slug>/`, and the loop applies it
on its next cycle. Runner processes (`ARK_ROLE`) cannot post: desk-guard denies them the folder and the commands refuse
them.

## Commands

| Command | What |
|---|---|
| `ark ps` | every open job: status, tier, time, tokens, cost, last message; and the open questions |
| `ark logs <job> [-f] [--kind worker]` | a process's log, readable; the archived one after it ends |
| `ark stop <job>` | stops its processes, and the job ends blocked; with no process, it blocks on the next cycle |
| `ark steer <job> "<instruction>"` | stops the worker and resumes its session with the instruction |
| `ark attach <job>` | hands the worker to you in an interactive session; on a clean exit it goes on to the gate |
| `ark answer [--list] [--run <repo>/<slug> <id> "<text>"]` | answers open questions, one by one |

`<job>` also takes the form `<repo>/<slug>:<job>`, needed when two live runs share a job name or the run has finished.

## Questions

| Kind | Asked when | Answers |
|---|---|---|
| worker | a worker ends BLOCKED and a tick cannot settle it from the plan | any text; `:stop` blocks the job |
| plan_changed | the plan changed after its approval and the run is held | `stop` ends the run; restoring or re-approving the plan closes it |
| cap_run | the run spent more than the sum of its budgets | `raise <usd>`, or `stop` to end the run |

`questions.md` at the Arkimedes root lists them. It is rewritten by the loop and never read back.

Ending a run stops every process and blocks every open job. No final PR is opened; what merged stays on `ark/<slug>`
and the worktrees stay.

## Notifications

`notify` in `workspace.json` is an argv; the message becomes its last argument. Empty means no notifier. Alerts are
always kept in `runs/alerts.jsonl`, whether or not the notifier works. Critical alerts: a question from a cap, a merge
refused, an integration gate that failed twice, an unreachable model, a run's end, and "nothing left to advance".

For the phone, for example:

```json
"notify": ["sh", "-c", "curl -fsS -m 8 -d \"$1\" https://ntfy.sh/<your-topic>", "notify"]
```

## Caps and anomalies

- Per run: the sum of the plan's job budgets. Over it, running workers finish, nothing new starts, and a question asks.
- Per day: `limits.usd_per_day`, against `runs/spend.jsonl`. Nothing new starts until the next day or a higher limit.
  An unreadable ledger counts as over.
- A worker that is over its tier's minutes, silent for 20 minutes, or over its tier's tokens gets an intervene tick,
  which chooses wait (once), steer (twice per job), stop and escalate, or stop and block. The process's hard deadline
  is twice its tier's minutes.

## Smoke on a real claude

Run these by hand after the merge; the suite uses a fake claude:

1. `onFailure` and the settings env reach the worker.
2. A SIGKILL of claude: does its shell survive?
3. A push to main from inside a worker is denied.
4. `ark attach` resumes the session and sees the work.
5. A `steer` resumes the session and the worker follows it.
6. The notifier reaches the Mac, and the phone once its service is chosen.
