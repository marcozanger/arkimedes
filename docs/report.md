# Run report and history

Every run ends with a report and leaves its numbers in `history/` (spec §9a). No model is involved.

## At close

Before the final PR, the loop writes `runs/<repo>/<slug>/steps.jsonl` and `report.html`, and it appends a
`## Report` summary to the final PR body. Once the run is `done` or `blocked`, it writes the report again,
records the run in `history/jobs.jsonl` and `history/plans.jsonl`, and rewrites `history/insights.md`.
The close alert ends with `· report: <path>`. If any of this fails, the run still closes and the event says
`report not written: …`.

## steps.jsonl

One row per step: every process in `done/` (worker, review, gate, integrate, judge, intervene, decisions),
every merge and every wait for Marco. Rows are rebuilt from the archived receipts and logs each time, so old
runs get them too.

```json
{"step": 3, "kind": "worker", "job": "shell", "attempt": 1, "tier": "T1", "model": "sonnet",
 "start": "…", "end": "…", "minutes": 9.4,
 "tokens": {"in": 30, "out": 12902, "cache_read": 766342, "cache_write": 50059}, "tokens_total": 62991,
 "usd": 0.41, "outcome": "done", "reason": "", "estimated": []}
```

- Tokens and model come from the result's `modelUsage`, which covers the whole session. The result's own
  `usage` is only the last turn. `tokens_total` = in + out + cache_write.
- `estimated` names the fields that were not measured. A log with no result line gets its tokens from the
  assistant lines. A merge whose event fell out of the 200 kept has no time.
- USD is the API-equivalent cost the CLI reports. On a subscription it is an estimate.

## Summary fields

- `estimated_minutes`: the plan's end-to-end estimate from `lib/ark/estimate.py` (same number `ark estimate`
  shows); the report puts it next to the total time.

## Commands

| Command | What |
|---|---|
| `ark report <repo>/<slug>` | rebuild steps.jsonl and report.html of any run. A closed run is also recorded in history |
| `ark report <repo>/<slug> --open` | same, then open report.html |
| `ark report --all` | every closed run, e.g. to backfill the history |
| `ark stats` | rewrite `history/insights.md` (at most 30 lines) |

Recording a run again replaces its rows, so `--all` can be run any time.

## history/

- `jobs.jsonl`: one row per job. It has repo, plan, job, tasks, area size, files and lines changed, size
  (S ≤ 3 files, M ≤ 10, L), start and end tier, attempts, steers, reviews, gate and integration failures,
  minutes, tokens, USD, budget, outcome and failure kind.
- `plans.jsonl`: one row per run with its totals.
- `insights.md`: by repo, merged per starting tier, median cost and time by size, actual against budget,
  and the top failures. The desk reads it before writing a manifest.

Not yet (P4b): `lessons.md` from the close tick, the report published as an Artifact, and per-tier limits
from the history p90.
