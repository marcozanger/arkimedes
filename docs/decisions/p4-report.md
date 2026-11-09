# Decisions — p4-report

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/p4-report/plan.md · approved 2026-11-07 · closed 2026-11-07
Jobs: 4 merged, 0 blocked · Decisions: 4 worker, 0 reviewer, 2 arkimedes

## steps — merged

- **D1** · Two of the plan's tests were adjusted. `build()` in the T1 tests now uses `jobs={}`, and the summary tests pass the intended USD to `receipt()` as well as to the log line. · _worker (T1)_
  <details><summary>more</summary>

  Why: The T1 tests' `build()` used a state with two joined jobs, so once T2 added join rows they no longer matched. The summary reads USD from the receipt, whose default is 0.25, so the plan's values never matched.
  Rejected: Changing the helper defaults or the source to match the tests.
  See: https://github.com/marcozanger/arkimedes/pull/11 · report `runs/arkimedes/p4-report/steps.report.md`
  </details>

## render — merged

- **D2** · In the two tests that assert `$1.25`, `usd=1.25` is passed to `receipt()` as well as to `result_line()`. · _worker (T1)_
  <details><summary>more</summary>

  Why: `steps.build` takes the cost from the receipt's `usd` field (default 0.25), not from the log, so the plan's tests as written failed.
  Rejected: Changing `tests/report_helpers.py`, which is outside the job's area. Changing how `steps` reads the cost.
  See: https://github.com/marcozanger/arkimedes/pull/13 · report `runs/arkimedes/p4-report/render.report.md`
  </details>

## history — merged

- **D3** · In the first history test, `usd=1.0` is passed to `receipt()` as well as to `result_line()`. · _worker (T1)_
  <details><summary>more</summary>

  Why: The helper takes cost from the receipt's own `usd` (default 0.25), so the plan's test as written got 0.25.
  Rejected: Changing `report_helpers`, which is outside the job's area.
  See: https://github.com/marcozanger/arkimedes/pull/12 · report `runs/arkimedes/p4-report/history.report.md`
  </details>

- **D4** · After the integration gate went green on `ef08a88e`, `ark/p4-report` had moved because render merged. The runner re-ran the gate and merged only after it was green on `10b01211`. · _arkimedes_
  <details><summary>more</summary>

  Why: The first green result no longer described the branch history would merge into.
  Rejected: Merging on the stale green result.
  See: https://github.com/marcozanger/arkimedes/pull/12 · report `runs/arkimedes/p4-report/history.report.md`
  </details>

## close — merged

- **D5** · `tests/test_report_close.py` reads alerts from `runs/alerts.jsonl` instead of `st["alerts"]`, which is what the plan's test used. · _worker (T2)_
  <details><summary>more</summary>

  Why: Each tick moves `st["alerts"]` into `runs/alerts.jsonl` (loop.py around line 1635), so after `drive()` the list is always empty and the plan's assertion could never pass.
  Rejected: Keeping alerts in the state, which would change the loop's alert delivery and is outside this job.
  See: https://github.com/marcozanger/arkimedes/pull/14 · report `runs/arkimedes/p4-report/close.report.md`
  </details>

- **D6** · The close worker went over its T2 time limit at 18:35. The intervene tick gave no usable answer, so the runner chose to wait. It then redirected the worker to re-run the job gate in the foreground, and the job merged after attempt 2 (resumed). · _arkimedes_
  <details><summary>more</summary>

  Why: The API connection dropped while the worker was reading a background gate result, so that turn was lost. The worker also never ran the full suite to the end, because its background run hit the 15-minute limit. Only the gate is verified for tests outside it.
  Rejected: Stopping or blocking the job, and a fresh attempt without the worker's context.
  See: https://github.com/marcozanger/arkimedes/pull/14 · report `runs/arkimedes/p4-report/close.report.md`
  </details>
