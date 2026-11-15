# Decisions — euklides-doable-asks

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/euklides-doable-asks/plan.md · approved 2026-11-13 · closed 2026-11-15
Jobs: 1 merged, 0 blocked · Decisions: 3 worker, 0 reviewer, 1 arkimedes

## doable-asks — merged

- **D1** · A run-level `_run` ask (the cap_run question) needs no `then`: `parse(..., then=False)` is used there, and a stray `then` is ignored. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: The `_run` question is outside this job and keeps its `raise <usd>|stop` answers.
  Rejected: Requiring `then` everywhere, which breaks the cap question. Rejecting a stray `then`, because one extra key would cost Marco Euklides' note.
  See: https://github.com/marcozanger/arkimedes/pull/91 · report `runs/arkimedes/euklides-doable-asks/doable-asks.report.md`
  </details>

- **D2** · A failed base fix asks with `then: fix_base`, so ok tries another fix with Marco's authority. A base fix started by ok from a non-integrate trigger sends the worker on once it lands, told to bring the fixed base in. It does not go to `needs_integrate`. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: Going on with the old proposal after a failed fix is the kind of ok the plan removes, and integrating unfinished work would be wrong.
  Rejected: `then: stop`, since Marco can already answer `:stop`.
  See: https://github.com/marcozanger/arkimedes/pull/91 · report `runs/arkimedes/euklides-doable-asks/doable-asks.report.md`
  </details>

- **D3** · `refuse_ok` is stored on the question dict as well as the ask, so `check_answer(q, text)` keeps its signature. The words `sí`/`Sí` are written as `í` escapes. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: `answers.py` is outside the job's area and must not change. `tests/test_english_only.py`, also outside the area, flags the accented words.
  Rejected: Changing the `check_answer` signature, and editing the out-of-area files.
  See: https://github.com/marcozanger/arkimedes/pull/91 · report `runs/arkimedes/euklides-doable-asks/doable-asks.report.md`
  </details>

- **D4** · The run passed its $8.00 budget ($9.16 spent after fix attempt 2), and Marco answered q1 with `raise 4.00`. This was Euklides' proposal, which was `raise 2.00`, doubled. The head bc17e17e was then pushed, passed the gate and passed review round 2. · _arkimedes_
  <details><summary>more</summary>

  Why: The job had already passed its gate and opened PR #91. Only the push, the gate and the second review remained, and Euklides' $0.80 margin could not cover even the overrun.
  Rejected: `stop`, which would have ended the run with the review fix unpushed. Euklides' proposed `raise 2.00`.
  See: https://github.com/marcozanger/arkimedes/pull/91 · report `runs/arkimedes/euklides-doable-asks/doable-asks.report.md`
  </details>
