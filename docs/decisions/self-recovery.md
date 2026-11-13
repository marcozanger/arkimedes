# Decisions — self-recovery

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/self-recovery/plan.md · approved 2026-11-12 · closed 2026-11-12
Jobs: 5 merged, 0 blocked · Decisions: 5 worker, 0 reviewer, 4 arkimedes

## passing-faults — merged

- **D1** · The reminder test reads the alerts that `cycle()` returns, not `state()["alerts"]` as the brief said. · _worker (T1)_
  <details><summary>more</summary>

  Why: a cycle drains `st["alerts"]` into its return value, so the state is always empty after a cycle.
  Rejected: reading `state()["alerts"]` as in the brief, which can never see an alert.
  See: https://github.com/marcozanger/arkimedes/pull/61 · report `runs/arkimedes/self-recovery/passing-faults.report.md`
  </details>

- **D2** · The job's gate stayed red on `tests/test_english_only.py` because the base `ark/self-recovery` lacked main's #60. Marco had main's `origin/main` (8ebdcd4, #60) pushed to `origin/ark/self-recovery` as a fast-forward. The job then merged it and reran only the gate, with no new code. · _arkimedes_
  <details><summary>more</summary>

  Why: the decision logs in `docs/decisions/` quote "más", and #60 fixes the guard in a file outside the job's area. The job had already gone over its $6 budget ($6.79 at the first ask), and Marco accepted the overrun. Asks q1–q6 covered this, and the job was told to stop if anything other than english-only failed.
  Rejected: editing the two decision docs outside the area; adding `tests/test_english_only.py` to the job's area.
  See: https://github.com/marcozanger/arkimedes/pull/61 · report `runs/arkimedes/self-recovery/passing-faults.report.md`
  </details>

- **D3** · The job was blocked three times for editing `tests/test_control_alerts.py`, `tests/test_control_questions.py` and `tests/test_sleep_clocks.py`, which are outside its area. Marco accepted the edits. The runner cannot change a running job's area, so Marco stopped the job, added the three files to the plan's area, re-approved the plan and resumed from commit 2b3e6df with no new worker work. · _arkimedes_
  <details><summary>more</summary>

  Why: the plan's behaviour (`waiting_dep` instead of pending, no `holders_since`, no alert when the worker model is unreachable) breaks those tests, and the integration gate fails without the edits. Answering "ok" to the area widening in q7, q8 and q10 did not take effect, so the same block repeated. Spend reached $38.11 of the $6 budget over 11 attempts. Run-level budget question q9 was answered `raise 10`.
  Rejected: another worker attempt with a bigger budget (q8); the edits on an unwidened area.
  See: https://github.com/marcozanger/arkimedes/pull/61 · report `runs/arkimedes/self-recovery/passing-faults.report.md`
  </details>

## ark-retry — merged

- **D4** · The two refusal tests check exit code 1 and the printed message instead of `pytest.raises(CtlError)`. · _worker (T1)_
  <details><summary>more</summary>

  Why: the `_cmd` decorator turns `CtlError` into a printed message and a return value of 1, so the exception never reaches the test.
  Rejected: raising from the command.
  See: https://github.com/marcozanger/arkimedes/pull/63 · report `runs/arkimedes/self-recovery/ark-retry.report.md`
  </details>

## loop-restart — merged

- **D5** · `_self_check` builds its error tail as `_clean(output[-600:])`, not the brief's longer expression. · _worker (T1)_
  <details><summary>more</summary>

  Why: same result, easier to read.
  Rejected: copying the brief's expression verbatim.
  See: https://github.com/marcozanger/arkimedes/pull/64 · report `runs/arkimedes/self-recovery/loop-restart.report.md`
  </details>

## euklides-sees-more — merged

- **D6** · The worker made a one-line edit outside the area: `tests/test_brief.py` line 339, from `count("untrusted data") == 2` to `== 4`. · _worker (T1)_
  <details><summary>more</summary>

  Why: the plan's two new fenced brief sections (earlier answers and decisions, last events) raise the count of "untrusted data" to 4. The change also landed on `ark/self-recovery`, and the worker merged that branch in without a rebase, so the file no longer differs from it.
  Rejected: dropping the fences, which the plan requires.
  See: https://github.com/marcozanger/arkimedes/pull/65 · report `runs/arkimedes/self-recovery/euklides-sees-more.report.md`
  </details>

- **D7** · The area block on `tests/test_brief.py` came up four times (q13–q16) and kept recurring after each "ok". The area in force never widened, and the runner reads it from the approved plan. Marco finally had the edit put on the integration branch `ark/self-recovery`, and the job branch then merged it so the file left the diff. · _arkimedes_
  <details><summary>more</summary>

  Why: a re-approved plan was not possible because jobs had already merged. Marco's `ark retry` answer to q14 and his "already changed by hand" answer to q15 did not clear the block, and the answer to q15 found no open question.
  Rejected: adding the file to the plan's area and re-approving (q13, q14); the worker editing it again (q15).
  See: https://github.com/marcozanger/arkimedes/pull/65 · report `runs/arkimedes/self-recovery/euklides-sees-more.report.md`
  </details>

## euklides-fixes — merged

- **D8** · The fix worker's branch is `ark/<slug>-_fix-<n>`, local only, never pushed to origin and with no PR. · _worker (T2)_
  <details><summary>more</summary>

  Why: a job id cannot start with `_`, so the name cannot collide with a job, and the brief excludes a PR for the fix.
  Rejected: a remote branch with a PR.
  See: https://github.com/marcozanger/arkimedes/pull/66 · report `runs/arkimedes/self-recovery/euklides-fixes.report.md`
  </details>

- **D9** · The first attempt failed the job gate on one test, `tests/test_loop.py::test_a_save_that_fails_after_a_receipt_starts_nothing_in_that_cycle`. Euklides retried without editing that test, which is outside the area and times out under load. Attempt 2 passed. · _arkimedes_
  <details><summary>more</summary>

  Why: the test polls the loop up to 600 times (about 48 s) and timed out when other gates ran on the machine. The worker showed the job adds no loop work when a run has no fixes, and the test passed alone 8 of 8 times. Switching the test to wall-clock waiting is left for a later plan.
  Rejected: editing the test in this job.
  See: https://github.com/marcozanger/arkimedes/pull/66 · report `runs/arkimedes/self-recovery/euklides-fixes.report.md`
  </details>
