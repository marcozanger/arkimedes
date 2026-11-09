# Decisions — wait-final-pr-2

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/wait-final-pr-2/plan.md · approved 2026-11-09 · closed 2026-11-09
Jobs: 1 merged, 0 blocked · Decisions: 2 worker, 0 reviewer, 2 arkimedes

## wait-final-pr — merged

- **D1** · A plan whose area overlaps a finished run's final PR now waits only while that PR is `OPEN`. A merged or closed PR is cached in `st["settled_prs"]` and not looked up again. · _worker (Sonnet 5.5)_
  <details><summary>más</summary>

  Why: an unmerged final PR means the base branch does not yet contain the earlier plan's changes, so the second plan would conflict or build on stale code. Caching settled PRs avoids repeated forge lookups.
  Rejected: waiting only on running or held runs (the previous behavior, which let overlapping plans start before the first final PR landed).
  See: https://github.com/marcozanger/arkimedes/pull/31 · report `runs/arkimedes/wait-final-pr-2/wait-final-pr.report.md`
  </details>

- **D2** · A `ForgeError` while reading the final PR makes the plan wait with its own `cannot read the final PR` reason. The reason includes the error text and the PR URL. · _worker (Sonnet 5.5)_
  <details><summary>más</summary>

  Why: failing closed avoids starting an overlapping plan when the PR state is unknown. Putting the error text in the reason tells Marco what failed. The reason only changes, and so only alerts again, when the error text changes.
  Rejected: a fixed reason with no detail; treating a forge error as "not open" and starting the plan.
  See: https://github.com/marcozanger/arkimedes/pull/31 · report `runs/arkimedes/wait-final-pr-2/wait-final-pr.report.md`
  </details>

- **D3** · The integration gate failed on the base commit `b28d1dd`, not because of this job. `tests/test_english_only.py` rejects `más` and other Spanish words in `docs/decisions/english-only-texts.md`, which is outside the job's area. The job went back to the worker and the worker raised question q1. · _arkimedes_
  <details><summary>más</summary>

  Why: the failure existed before the job's changes, and fixing it would mean editing files the plan did not give this job.
  Rejected: widening the job's area so the worker could edit the file; allowlisting the words in the test.
  See: https://github.com/marcozanger/arkimedes/pull/31 · report `runs/arkimedes/wait-final-pr-2/wait-final-pr.report.md`
  </details>

- **D4** · Marco answered q1 with "I'll do it, you keep working in yours". He fixes the Spanish text in `docs/decisions/english-only-texts.md` himself, and the worker leaves it alone and stays inside its own area. · _arkimedes_
  <details><summary>más</summary>

  Why: this keeps the job's area as planned and puts the fix with the owner of that file. The worker resumed as attempt 3, the job gate and review passed, and the job merged.
  Rejected: changing `más` to `more` inside this job; widening the job's area.
  See: https://github.com/marcozanger/arkimedes/pull/31 · report `runs/arkimedes/wait-final-pr-2/wait-final-pr.report.md`
  </details>
