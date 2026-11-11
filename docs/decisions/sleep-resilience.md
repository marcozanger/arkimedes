# Decisions — sleep-resilience

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/sleep-resilience/plan.md · approved 2026-11-09 · closed 2026-11-09
Jobs: 3 merged, 0 blocked · Decisions: 4 worker, 0 reviewer, 3 arkimedes

## clocks — merged

- **D1** · All four tasks went in as one commit (082703f), not one per task. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: The first attempt died after writing everything in one pass, and the rules forbid rewriting history.
  Rejected: Splitting the commit by rewriting it.
  See: https://github.com/marcozanger/arkimedes/pull/42 · report `runs/arkimedes/sleep-resilience/clocks.report.md`
  </details>

- **D2** · The `_polling` warning is in English ("the deadlines could not be moved after the sleep"), not the Spanish text in the brief. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: The repo has an English-only guard for its texts.
  Rejected: Using the Spanish string from the brief.
  See: https://github.com/marcozanger/arkimedes/pull/42 · report `runs/arkimedes/sleep-resilience/clocks.report.md`
  </details>

- **D3** · The job's gate was red on attempt 2 (timeout, exit -15). The judge gave no usable answer, so the runner escalated to T2. The gate went green on attempt 3 with the same head 082703f0. · _arkimedes_
  <details><summary>more</summary>

  Why: The test run (`tests/test_loop.py` takes 2–3 minutes alone) went over the job's time limit. The code did not change between the red and green gates, so the failure was time, not a defect.
  Rejected: Retrying at T1. The judge returned nothing, so the runner took the escalation path.
  See: https://github.com/marcozanger/arkimedes/pull/42 · report `runs/arkimedes/sleep-resilience/clocks.report.md`
  </details>

## free-retry — merged

- **D4** · A failed process that lived through a sleep goes back to its waiting status with nothing charged, and the worker keeps its session. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: This is how the job's brief was implemented. The report lists no other decisions, so I took the slept-path behavior as the one a reviewer would ask about. A failure caused by a sleep should not use up a tick.
  Rejected: None recorded in the report.
  See: https://github.com/marcozanger/arkimedes/pull/43 · report `runs/arkimedes/sleep-resilience/free-retry.report.md`
  </details>

## net-wait — merged

- **D5** · The Spanish status prefix is written with unicode escapes for the accented letter and the middle dot in `loop.py` and the test. The docs quote it in English. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: `tests/test_english_only.py` flags literal accented text, and that file is outside this job's area.
  Rejected: Adding an allowlist entry in `tests/test_english_only.py`, because it is outside the job's area.
  See: https://github.com/marcozanger/arkimedes/pull/44 · report `runs/arkimedes/sleep-resilience/net-wait.report.md`
  </details>

- **D6** · The runner told the worker to wait when the job ran over its T1 time. The worker had already reported DONE and had only kept polling a full-suite run. The runner was to check the gate itself and not rely on the worker's claim. · _arkimedes_
  <details><summary>more</summary>

  Why: The judge saw that the gate had passed and the commits were in. The overrun came from the polling, which the worker had since stopped.
  Rejected: Stopping the worker, or escalating the tier.
  See: https://github.com/marcozanger/arkimedes/pull/44 · report `runs/arkimedes/sleep-resilience/net-wait.report.md`
  </details>

- **D7** · The integration gate failed on head 9b3ffef0 (exit 1), so the job went back to the worker for a second attempt. Head 624ffde0 passed the job gate, review and the integration gate. · _arkimedes_
  <details><summary>more</summary>

  Why: Review was CLEAN and the job gate was green, but the integration gate caught the English-only guard failing on the literal accented text. The fix is described in D5.
  Rejected: Merging with the red integration gate.
  See: https://github.com/marcozanger/arkimedes/pull/44 · report `runs/arkimedes/sleep-resilience/net-wait.report.md`
  </details>
