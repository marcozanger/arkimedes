# Decisions — euklides-options

Plan: /Users/marcozanger/.claude/arkimedes/approvals/arkimedes/euklides-options/v1/plan.md · approved 2026-11-22 · closed 2026-11-23
Jobs: 3 merged, 0 blocked · Decisions: 7 worker, 0 reviewer, 2 arkimedes

## question-options — merged

- **D1** · A Euklides ask with neither `then` nor a proposal offers only the stop option. · _worker (T2)_
  <details><summary>more</summary>

  Why: an `ok` option would resume the worker with an empty proposal.
  Rejected: an `ok` option with no proposal behind it.
  See: https://github.com/marcozanger/arkimedes/pull/132 · report `runs/arkimedes/euklides-options/question-options.report.md`
  </details>
- **D2** · When a question has options, any bare number out of range (0 included) is rejected with "no option N". With no options, a number stays plain text. · _worker (T2)_
  <details><summary>more</summary>

  Why: a number aimed at a button should never reach a worker as an instruction.
  Rejected: rejecting only numbers above the option count.
  See: https://github.com/marcozanger/arkimedes/pull/132 · report `runs/arkimedes/euklides-options/question-options.report.md`
  </details>

## euklides-alternatives — merged

- **D3** · With `ok 2`, the alternative's label becomes the ask's `proposal`. · _worker (T2)_
  <details><summary>more</summary>

  Why: a `go_on` alternative has no proposal of its own, and the worker should not get the first option's instruction.
  Rejected: a separate `proposal` key on the alternative (the plan fixes its keys).
  See: https://github.com/marcozanger/arkimedes/pull/133 · report `runs/arkimedes/euklides-options/euklides-alternatives.report.md`
  </details>
- **D4** · When the first option's ok is refused (a widen into another job's area), the alternative is still offered, as option 1 before stop. · _worker (T2)_
  <details><summary>more</summary>

  Why: it is a separate, valid path, and `ok 2` still names it by text.
  Rejected: hiding the alternative whenever ok is refused.
  See: https://github.com/marcozanger/arkimedes/pull/133 · report `runs/arkimedes/euklides-options/euklides-alternatives.report.md`
  </details>
- **D5** · An alternative must have a non-empty `label`; one without is dropped and the ask is kept. · _worker (T2)_
  <details><summary>more</summary>

  Why: the plan lists label as required (`files?`, `usd?`, `gate?` are the optional keys), and it is the button text.
  Rejected: falling back to `_then_text` for the label.
  See: https://github.com/marcozanger/arkimedes/pull/133 · report `runs/arkimedes/euklides-options/euklides-alternatives.report.md`
  </details>
- **D6** · The job's first worker attempt ended FAILED on gate timeouts under machine load (avg ~200). Euklides found no code change was needed, and the job went on once the gate was green on a quieter machine. · _arkimedes_
  <details><summary>more</summary>

  Why: test_ui_screens, test_proc and test_merge failed only on timing outside the job's files, and each passed alone. The job's own tests passed.
  Rejected: changing code to fix a load problem. A later review round (1 finding, a NUL in the alternative's label) was fixed in attempt 2.
  See: https://github.com/marcozanger/arkimedes/pull/133 · report `runs/arkimedes/euklides-options/euklides-alternatives.report.md`
  </details>

## ui-option-buttons — merged

- **D7** · The existing typed-answer UI test now targets a new option-less question `q3` in the fixture. · _worker (T1)_
  <details><summary>more</summary>

  Why: `q2` has options and so hides its textarea, and the brief needs both kinds of question in the fixture.
  Rejected: dropping the typed-answer test.
  See: https://github.com/marcozanger/arkimedes/pull/134 · report `runs/arkimedes/euklides-options/ui-option-buttons.report.md`
  </details>
- **D8** · The answered list shows the option label in bold and the raw answer as small muted text when they differ. · _worker (T1)_
  <details><summary>more</summary>

  Why: the label is what Marco chose, and the text stays traceable.
  Rejected: showing only the label.
  See: https://github.com/marcozanger/arkimedes/pull/134 · report `runs/arkimedes/euklides-options/ui-option-buttons.report.md`
  </details>
- **D9** · The job's first attempt failed on headless-Chrome timeouts under load (130–250). Euklides raised `_dump`'s Chrome subprocess timeout from 90 s to 300 s (b48ae25) and left the checks unchanged. · _arkimedes_
  <details><summary>more</summary>

  Why: tests/test_ui_screens.py passed 15/15 alone and with -n auto, so the failure was load, not the change.
  Rejected: re-running only on a quieter machine, as was done for euklides-alternatives (D6). Here the timeout was raised in code.
  See: https://github.com/marcozanger/arkimedes/pull/134 · report `runs/arkimedes/euklides-options/ui-option-buttons.report.md`
  </details>
