# `ark review`

Marco reviews Euklides' decisions one at a time, with no model.

`ark review [<repo>/<slug>] [--all] [--no-distill]` lists the decisions that have no review and no
Marco answer, oldest first. Each card shows the run, job, trigger, the job's outcome from the run
state (`merged`, `blocked`, `still running`, `run gone`), the situation, what Euklides decided and, if
any, its undo and proposal.

## Replies

- `ok` — the decision was right.
- `mal: <comment>` — it was wrong; the comment is required and says why.
- `skip` — leave it for a later review.
- `q` — stop.

Anything else is asked again.

## Options

- `--all` — also decisions already reviewed.
- `<repo>/<slug>` — only that run's decisions.
- `--no-distill` — record verdicts but do not distill the lessons.

## Files under `history/euklides/`

- `decisions.jsonl` — every decision Euklides made (written by the runner).
- `reviews.jsonl` — Marco's verdicts.
- `lessons.md` — the rules Euklides reads on every call, at most 30, each citing decision ids.
- `lessons.prev.md` — `lessons.md` as it was before the last distill.

When new verdicts were recorded, one Sonnet call rewrites `lessons.md` from the current lessons plus the
last 200 reviewed decisions, and the diff is printed. If the call fails or returns nothing usable,
`lessons.md` is left untouched and the command exits 1.

Marco may edit `lessons.md` by hand: the distill keeps existing rules unless a newer verdict
contradicts them.
