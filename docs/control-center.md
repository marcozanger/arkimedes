# Control center

The control center is a web page that shows every plan the runner knows: its jobs, timeline,
decisions, events and plan file, with what it cost and how long it worked against its budget.
`ark ui` serves it; the page reads one JSON snapshot (the same as `ark runs --json`).

## Start it

```
ark ui --open          # serve and open the browser tab
ark ui --port 8800     # another port (default 8737)
```

It listens on `127.0.0.1` only and rejects a foreign `Host` header. `GET` reads; the only `POST`s
are the five writes below. If the port is busy it exits 2 and names `--port`. Ctrl-C stops it.

The page polls `/api/runs` every 10 seconds. The page file is read on every request, so a
`git pull` shows up on reload.

## Screens

Home (every plan on one line), a plan screen (Timeline, Questions, Events, Decisions, Spec, Plan), a draft
screen (spec and plan reviewed per section and approved), History and Euklides.

The Decisions tab of a plan shows its `runs/<repo>/<slug>/decisions.md` (written when the run closes) one
decision at a time with OK / Change (a Change needs a comment) and a closing question, "does this run need a
fix or more work?". Verdicts are the same rows `ark review` writes; see `docs/review.md`.

## Endpoints

Reads (`GET`): `/api/runs`, `/api/drafts`, `/api/draft`, `/api/history`, `/api/euklides`, `/api/image`, `/api/decision-review?repo=&slug=`.

The five writes (`POST`, JSON body), each through the same code the terminal commands use:

| Endpoint | Write |
|---|---|
| `/api/review` | a section's verdict and note, into `review.json` |
| `/api/approve-spec` | the spec's `status: approved` (every section must be `ok`) |
| `/api/approve-plan` | the plan's approval, like the terminal command (valid plan, spec approved, every section `ok`, sha unchanged) |
| `/api/answer` | an answer to an open question, like `ark answer` |
| `/api/decision-review` | OK / Change on one decision of a closed run's `decisions.md`, or the run's follow-up (`none`, `fix`, `more`), into `history/euklides/reviews.jsonl`; refused from a runner process (`ARK_ROLE`) |

## Token and Origin rule

Every `POST` must carry a `Host` of `127.0.0.1:<port>` or `localhost:<port>`, an `Origin` of
`http://127.0.0.1:<port>` or `http://localhost:<port>`, and an `X-Ark-Token` header equal to the
token the server generated at start and put in the page it serves. Otherwise it answers 403. A
process with `ARK_ROLE` set (a runner worker) cannot approve plans or answer.

## What the server writes

Only: `review.json` in a draft's folder, the `status` of a `spec.md`, the plan approval, and
answers to a run's questions. Nothing else.

## review.json

```json
{
  "version": 1,
  "docs": {
    "spec": {
      "sha256": "<hash of the whole file>",
      "sections": {
        "<key>": {"verdict": "ok", "note": "", "at": "2026-11-13T10:00:00+00:00", "hash": "<hash of the section>"}
      }
    },
    "plan": {"sha256": "...", "sections": {}}
  }
}
```

A section is a `## ` block outside code fences; its `<key>` is the lowercased dashed title (`-2`
for a repeat). `verdict` is `ok` or `change`; `note` (up to 4000 characters) says what to change. A
verdict counts only while the section's `hash` is the current one: editing a section resets that
section and no other. `ark-plan` resuming a slug reads this file and works the `change` sections
first.

## Notifications

The page asks for browser notification permission through a button; nothing is asked before the
click. Notifications fire for the events that need Marco (blocked jobs, held plans, critical
alerts, plans waiting for approval or ready to close), once each.

## The `cost` block

Every plan in the snapshot has `cost`:

| Field | Meaning |
|---|---|
| `usd` | sum of the jobs' spend (a closed plan: the report's number) |
| `budget_usd` | sum of the jobs' budgets (a closed plan: the report's number) |
| `closed` | true when the plan is `done` or `blocked` and `history/plans.jsonl` has its row |
| `wall_min`, `working_min`, `waiting_min` | whole minutes from that row; `null` while open |
| `estimated_min` | estimated end minus start, in minutes; `null` when either is unknown |

## Trying it

1. Start it: `ark ui --open`. The page opens in a browser tab and shows the plans within a poll.
2. Click `Turn on notifications` in the sidebar and allow them. If the browser says no, the sidebar
   reads `Notifications are off in this browser` and the rest of the page still works.
3. Without a runner, feed the page by hand from the browser console:
   `update(sampleJson, {stale: false, error: ""})` shows the contents of `ui/sample.json`.
4. End-to-end check, on a real run in `ark-sandbox`:
   - force one job to `blocked` and confirm a notification arrives for that plan;
   - kill `ark-loop` and confirm `ark-loop is not running` arrives, once;
   - click the first notification: the tab comes forward and opens that plan (the URL hash
     becomes `#<repo>/<slug>/timeline`);
   - look at the page in light and dark mode, in Safari and in one other browser.

This check is Marco's, after the final integration. It needs a browser and a real run, so the
test suite does not cover it.
