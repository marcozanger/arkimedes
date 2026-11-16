# Decisions — flaky-gates

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/flaky-gates/plan.md · approved 2026-11-13 · closed 2026-11-16
Jobs: 3 merged, 0 blocked · Decisions: 5 worker, 0 reviewer, 3 arkimedes

## gate-slots — merged

- **D1** · Asked whether `tests/test_paths.py` could join the job's area so the worker could add `"gates_total": 2` to the expected limits dict (line 56). Marco answered ok and the area was widened. · _arkimedes_
  <details><summary>more</summary>

  Why: the job's gate runs that test, and it fails without the new key. The file is in no other job's area.
  Rejected: leaving the test failing; the worker editing outside its area without approval.
  See: https://github.com/marcozanger/arkimedes/pull/100 · report `runs/arkimedes/flaky-gates/gate-slots.report.md`
  </details>

- **D2** · Refresh checks and gates also count toward `gates_total`. They are heavy only when the plan is heavy. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: they run the suite too.
  Rejected: leaving them uncounted.
  See: https://github.com/marcozanger/arkimedes/pull/100 · report `runs/arkimedes/flaky-gates/gate-slots.report.md`
  </details>

- **D3** · The load brake check stays before the slot check in `Slots.gate`, and `gates_total` is read with `.get(..., 2)`. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: existing brake tests and limits dicts without the key keep working.
  Rejected: the brief's simpler `gate(heavy)`, which the repo code no longer matches.
  See: https://github.com/marcozanger/arkimedes/pull/100 · report `runs/arkimedes/flaky-gates/gate-slots.report.md`
  </details>

- **D4** · The integration gate failed on `test_a_worker_over_its_tokens_reaches_the_tick`. Euklides unblocked the job with no code change, and the job merged after a green integration gate. · _arkimedes_
  <details><summary>more</summary>

  Why: the test also fails on `ark/flaky-gates` without this job. It passed 3 of 3 alone on the job's branch, and the whole file passed under load. The failure is a timing flake.
  Rejected: changing the code; blocking the job.
  See: https://github.com/marcozanger/arkimedes/pull/100 · report `runs/arkimedes/flaky-gates/gate-slots.report.md`
  </details>

## regate — merged

- **D5** · The normal gate path in `_next_step` keeps the load brake (`slots.gate(heavy, c.st, job)`). The quiet re-run bypasses it through `Slots.regate()`. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: the load brake was added after the plan was written. A regate already waits for the machine to be free of gates and has its own deadline.
  Rejected: the plan's literal `slots.gate(heavy)` call, which would have dropped the load brake for every gate.
  See: https://github.com/marcozanger/arkimedes/pull/101 · report `runs/arkimedes/flaky-gates/regate.report.md`
  </details>

- **D6** · The job-gate replay was built end to end, with no fallback to `bare()`. An integration gate fails once and creates `busy`, then `FAKE_EMPTY=shell:2` brings the job back to its gate on the same head. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: the plan asked for this replay when the fake worker's switches allow it, and they do.
  Rejected: a `bare()`-only test of that path.
  See: https://github.com/marcozanger/arkimedes/pull/101 · report `runs/arkimedes/flaky-gates/regate.report.md`
  </details>

## flaky-record — merged

- **D7** · The new tests redirect `Path.home` to `tmp_path`. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: the suite's sandbox home is shared, so queue files from different tests collided.
  Rejected: unique repo names per test.
  See: https://github.com/marcozanger/arkimedes/pull/102 · report `runs/arkimedes/flaky-gates/flaky-record.report.md`
  </details>

- **D8** · The integration gate went red on five tests (`test_loop_closed_at` x3, `test_loop_final_pr` overlapping-plan x2). Euklides told the worker not to change its code, and the job merged after a green integration gate. · _arkimedes_
  <details><summary>more</summary>

  Why: all five were `drive()` timeouts (`tests/test_loop.py:55`). Under the full-suite run, the fake run did not get past `shell needs_gate` in about 20s. The failure was load, not the job's code.
  Rejected: changing the job's code to fix the tests.
  See: https://github.com/marcozanger/arkimedes/pull/102 · report `runs/arkimedes/flaky-gates/flaky-record.report.md`
  </details>
