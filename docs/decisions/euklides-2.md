# Decisions — euklides-2

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/euklides-2/plan.md · approved 2026-11-09 · closed 2026-11-09
Jobs: 3 merged, 0 blocked · Decisions: 5 worker, 0 reviewer, 1 arkimedes

## euklides-core — merged

- **D1** · test_brief.py got a `euk()` helper that builds its plan in a temp dir, because the `plan_obj()` the brief assumed does not exist. · _worker (T1)_
  <details><summary>more</summary>

  Why: the brief's tests called `plan_obj()`, which is not in the code, so the plan had to be built some other way.
  Rejected: a fixture, which would have changed the test signatures given in the brief.
  See: https://github.com/marcozanger/arkimedes/pull/35 · report `runs/arkimedes/euklides-2/euklides-core.report.md`
  </details>

## review-cmd — merged

- **D2** · The `ark review` card ends with the reply hint line (`ok · mal: <comment> · skip · q`) as well as the re-ask hint. · _worker (T1)_
  <details><summary>more</summary>

  Why: the brief's test expects the hint at least 3 times for two bad replies plus one good one.
  Rejected: printing the hint only on bad replies, which fails the test.
  See: https://github.com/marcozanger/arkimedes/pull/36 · report `runs/arkimedes/euklides-2/review-cmd.report.md`
  </details>

- **D3** · The distill RULES text says "with at most 30 rules" in lowercase, not "At most" as in the brief's text. · _worker (T1)_
  <details><summary>more</summary>

  Why: the brief's own test searches the prompt for "at most 30", and the brief's text did not match it.
  Rejected: changing the test.
  See: https://github.com/marcozanger/arkimedes/pull/36 · report `runs/arkimedes/euklides-2/review-cmd.report.md`
  </details>

## loop-wiring — merged

- **D4** · End-to-end tests whose job used to end `blocked` after a fixable block now answer `:stop` to Euklides' question, through a `stopping()` helper. · _worker (T2)_
  <details><summary>more</summary>

  Why: those blocks now ask Marco before blocking, and the tests are about what happens after the block.
  Rejected: a fake Euklides that blocks without asking, because no such action exists.
  See: https://github.com/marcozanger/arkimedes/pull/37 · report `runs/arkimedes/euklides-2/loop-wiring.report.md`
  </details>

- **D5** · An integration gate that failed twice is no longer listed as a critical alert in docs/runner-control.md. · _worker (T2)_
  <details><summary>more</summary>

  Why: that failure now goes to Euklides, and Marco hears about it only through a question.
  Rejected: keeping the alert, which would duplicate the question.
  See: https://github.com/marcozanger/arkimedes/pull/37 · report `runs/arkimedes/euklides-2/loop-wiring.report.md`
  </details>

- **D6** · The loop-wiring worker went silent for over 20 minutes after finishing, and the run sent a steer to end it. The steer was not delivered because the worker had already ended, and the run carried on with the pushed head e68119a5. The first review of PR #37 gave no verdict, so it was re-run and came back CLEAN. · _arkimedes_
  <details><summary>more</summary>

  Why: the worker's own report says its commit was in and the suite was green. The stuck process was most likely an open background task, so the work was treated as complete and the job gate then passed.
  Rejected: restarting the worker, which was not needed because the work was already pushed.
  See: https://github.com/marcozanger/arkimedes/pull/37 · report `runs/arkimedes/euklides-2/loop-wiring.report.md`
  </details>
