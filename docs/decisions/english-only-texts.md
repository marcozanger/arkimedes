# Decisions — english-only-texts

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/english-only-texts/plan.md · approved 2026-11-08 · closed 2026-11-08
Jobs: 3 merged, 0 blocked · Decisions: 5 worker, 0 reviewer, 1 arkimedes

## report-html — merged

- **D1** · Removed the `STATUS_ES` lookup instead of renaming it, because the English status labels equal the status values and the lookup did nothing. · _worker (Sonnet 5.5)_
  <details><summary>más</summary>

  Why: simplest option that fits the plan.
  Rejected: keeping a `STATUS_LABELS` identity map.
  See: https://github.com/marcozanger/arkimedes/pull/27 · report `runs/arkimedes/english-only-texts/report-html.report.md`
  </details>

## cli-output — merged

- **D2** · Reworded the proc.py stop notes in plain English instead of translating them word for word. The tests assert English fragments. · _worker (Sonnet 5.5)_
  <details><summary>más</summary>

  Why: keeps the meaning and the suggested commands.
  Rejected: a literal translation, which reads badly.
  See: https://github.com/marcozanger/arkimedes/pull/28 · report `runs/arkimedes/english-only-texts/cli-output.report.md`
  </details>

- **D3** · The worker went silent for over 20 minutes after saying STATUS: DONE. The run steered it to confirm that the changes and report were committed and to end the session. The resumed attempt 2 finished with head d14888fd and the job merged. · _arkimedes_
  <details><summary>más</summary>

  Why: the output looked stuck, and the work was probably finished already.
  Rejected: none recorded.
  See: https://github.com/marcozanger/arkimedes/pull/28 · report `runs/arkimedes/english-only-texts/cli-output.report.md`
  </details>

## records — merged

- **D4** · The allowlist in the new English-only guard test is longer than the brief's list. It also covers non-ASCII test data, the `para` variable in report.py, the Spanish trigger phrases in the ark-plan skill, and the `"sí"` quote in the old p1 spec. · _worker (Sonnet 5.5)_
  <details><summary>más</summary>

  Why: these are real hits of the guard, and all are outside this job's area or deliberate.
  Rejected: editing lib/ or the tests, which is outside the area. Dropping `"sí"` from the spec quote, which would no longer match cli.py.
  See: https://github.com/marcozanger/arkimedes/pull/29 · report `runs/arkimedes/english-only-texts/records.report.md`
  </details>

- **D5** · The `diseño` example is allowlisted at tests/test_proc.py, where it actually lives, because the brief did not name the file. · _worker (Sonnet 5.5)_
  <details><summary>más</summary>

  Why: that is the file that contains it.
  Rejected: tests/test_validate.py.
  See: https://github.com/marcozanger/arkimedes/pull/29 · report `runs/arkimedes/english-only-texts/records.report.md`
  </details>

- **D6** · The run flagged the worker as over its T1 time while it waited on the full pytest suite (over 10 minutes). It first waited one more round, then steered the worker to stop its background polling, confirm that the work was committed and the tests passed, and finish. Attempt 2 finished with head 6d935f01 and the job merged. · _arkimedes_
  <details><summary>más</summary>

  Why: the worker had written its report and was only waiting on the slow suite, so it looked slow rather than stuck. It was still running after the extra round.
  Rejected: stopping the worker at the first overrun.
  See: https://github.com/marcozanger/arkimedes/pull/29 · report `runs/arkimedes/english-only-texts/records.report.md`
  </details>
