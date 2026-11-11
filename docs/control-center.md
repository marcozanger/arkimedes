# Control center

The control center is a web page that shows every plan the runner knows: its jobs, timeline,
decisions, events and plan file, with what it cost and how long it worked against its budget.
`ark ui` serves it; the page reads one JSON snapshot (the same as `ark runs --json`).

## Start it

```
ark ui --open          # serve and open the browser tab
ark ui --port 8800     # another port (default 8737)
```

It listens on `127.0.0.1` only, answers `GET` only, rejects a foreign `Host` header, and never
writes a file. If the port is busy it exits 2 and names `--port`. Ctrl-C stops it.

The page polls `/api/runs` every 10 seconds. The page file is read on every request, so a
`git pull` shows up on reload.

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
