# Decisions — euklides-hands

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/euklides-hands/plan.md · approved 2026-11-11 · closed 2026-11-13
Jobs: 3 merged, 0 blocked · Decisions: 5 worker, 1 reviewer, 1 arkimedes

## settings — merged

- **D1** · A non-object `euklides` settings block also raises a ValueError that names `euklides`. · _worker (T1)_
  <details><summary>more</summary>

  Why: nothing can be read from a non-object block, so failing early is the only usable outcome.
  Rejected: ignoring the block and using defaults.
  See: https://github.com/marcozanger/arkimedes/pull/70 · report `runs/arkimedes/euklides-hands/settings.report.md`
  </details>

- **D2** · The integration gate failed (exit 1) after a CLEAN review, and the job went back to the worker for a second attempt. The same head `19110b23` was pushed again, and the gate then passed on `656625d1`. · _arkimedes_
  <details><summary>more</summary>

  Why: a red integration gate sends the job back to its worker. The report does not say what failed or what changed between attempts.
  Rejected: none recorded.
  See: https://github.com/marcozanger/arkimedes/pull/70 · report `runs/arkimedes/euklides-hands/settings.report.md`
  </details>

## hands — merged

- **D3** · A worker's DONE now also fails when its worktree is not on the job's branch. This comes from sharing one `loop.done_check` between a worker's DONE and Euklides' `fixed`. · _worker (T2)_
  <details><summary>more</summary>

  Why: the plan asks for one shared check, and the branch check is part of it.
  Rejected: skipping the branch check for workers, which would keep two separate checks.
  Open follow-up: `hooks/area-guard.py` (outside this job's area) denies Write and Edit to every role but `worker`, so Euklides' Edit and Write tools are refused. It can still edit through Bash. A follow-up should let `ARK_ROLE=euklides` write inside its worktree.
  See: https://github.com/marcozanger/arkimedes/pull/74 · report `runs/arkimedes/euklides-hands/hands.report.md`
  </details>

- **D4** · A `fixed` reply counts toward `euklides_count` even when its check fails. · _worker (T2)_
  <details><summary>more</summary>

  Why: the plan says the count includes a `fixed`, and a failed repair was still spent.
  Rejected: counting only a passing `fixed`.
  See: https://github.com/marcozanger/arkimedes/pull/74 · report `runs/arkimedes/euklides-hands/hands.report.md`
  </details>

- **D5** · Review round 1 raised one finding. A `fixed` from a job with no worktree (Euklides ran in the ticks folder) now asks Marco instead of raising in `done_check`. · _reviewer_
  <details><summary>more</summary>

  Why: raising an exception there would stop the loop on a case that Marco can resolve.
  Rejected: letting `done_check` raise.
  See: https://github.com/marcozanger/arkimedes/pull/74 · report `runs/arkimedes/euklides-hands/hands.report.md`
  </details>

## budget — merged

- **D6** · Marco's `ok` to a job raise adds to `usd_raised` and to a new `marco_raised`. The margin left is `margin × budget − (usd_raised − marco_raised)`. · _worker (T2)_
  <details><summary>more</summary>

  Why: the brief's formula uses `usd_raised` for the margin, and Marco's raise must not use up Euklides' margin.
  Rejected: a separate `euklides_raised` per job, which would change the formula the brief names.
  See: https://github.com/marcozanger/arkimedes/pull/78 · report `runs/arkimedes/euklides-hands/budget.report.md`
  </details>

- **D7** · Run-level Euklides calls are also capped by `decisions_per_job` (`st.euklides_run_count`). An unusable answer opens the `cap_run` question. · _worker (T2)_
  <details><summary>more</summary>

  Why: tiny raises could otherwise call him again and again within the margin.
  Rejected: no count, which leaves the calls unbounded.
  See: https://github.com/marcozanger/arkimedes/pull/78 · report `runs/arkimedes/euklides-hands/budget.report.md`
  </details>
