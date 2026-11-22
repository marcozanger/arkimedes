# Decisions — hung-worker

Plan: /Users/marcozanger/.claude/arkimedes/approvals/arkimedes/hung-worker/v1/plan.md · approved 2026-11-21 · closed 2026-11-22
Jobs: 2 merged, 0 blocked · Decisions: 5 worker, 0 reviewer, 2 arkimedes

## api-hang — merged

- **D1** · The quiet window for an API-hung worker is measured from the latest of the log's mtime, `watch_from` and `woke_at`, not from the log's mtime alone. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: a worker that has just started or just woke is never resumed on an old error line.
  Rejected: the log's mtime alone.
  See: https://github.com/marcozanger/arkimedes/pull/129 · report `runs/arkimedes/hung-worker/api-hang.report.md`
  </details>

- **D2** · In `on_intervene()`, a tick counts as "never spoke" when `_learned_nothing(r)` holds or `r["spoke"] is False`, and the worker is then resumed with the anomaly as the reason text. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: this covers both the quick death and the silent timeout, while a tick that wrote anything keeps today's fallback.
  Rejected: checking only the timeout reason.
  See: https://github.com/marcozanger/arkimedes/pull/129 · report `runs/arkimedes/hung-worker/api-hang.report.md`
  </details>

- **D3** · A crash that loses the `api-hung` receipt sends the job back to `needs_worker` with the resume text as its note (`_gone_worker`). · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: this mirrors how a lost steer is handled.
  Rejected: treating it as a failure.
  See: https://github.com/marcozanger/arkimedes/pull/129 · report `runs/arkimedes/hung-worker/api-hang.report.md`
  </details>

- **D4** · The job gate was red because two existing tests compare whole dicts that this plan extends (`tests/test_paths.py` limits defaults, `tests/test_control_usage.py` `scan()` result). The worker asked to widen the area to those two files, and Marco answered ok. The worker then added the new keys to the expected dicts. · _arkimedes_
  <details><summary>more</summary>

  Why: both files are outside the job's area, so the worker could not fix the gate alone. The job waited in `waiting_answer` from 2026-11-21 22:02 until 2026-11-22 12:55.
  Rejected: stopping the job (`:stop`).
  See: https://github.com/marcozanger/arkimedes/pull/129 · report `runs/arkimedes/hung-worker/api-hang.report.md`
  </details>

- **D5** · The run's base was held at `ef78221b` because main was red (`tests/test_loop.py::test_a_save_that_fails_after_a_receipt_starts_nothing_in_that_cycle`). It was refreshed to `4a13e600` only after main merged into `ark/hung-worker` was green. · _arkimedes_
  <details><summary>more</summary>

  Why: starting jobs on a red base would blame the jobs for a failure that came from main.
  Rejected: starting jobs on the red base.
  See: run events 2026-11-21 15:18 and 21:51
  </details>

## ps-report — merged

- **D6** · In `ark ps`, the resume text replaces the worker's last log message, but not a question or a `waiting_*` text. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: the plan says "unless a question or a wait already fills it".
  Rejected: appending to the log text.
  See: https://github.com/marcozanger/arkimedes/pull/130 · report `runs/arkimedes/hung-worker/ps-report.report.md`
  </details>

- **D7** · The report step row for a resume is kind `resume`, model `script`, outcome `resumed`, with the error text in `reason`. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: it mirrors the merge row.
  Rejected: a new outcome per error.
  See: https://github.com/marcozanger/arkimedes/pull/130 · report `runs/arkimedes/hung-worker/ps-report.report.md`
  </details>
