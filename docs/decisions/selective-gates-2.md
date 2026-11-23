# Decisions — selective-gates-2

Plan: /Users/marcozanger/.claude/arkimedes/approvals/arkimedes/selective-gates-2/v1/plan.md · approved 2026-11-23 · closed 2026-11-23
Jobs: 2 merged, 0 blocked · Decisions: 5 worker, 0 reviewer, 2 arkimedes

## gate-select — merged

- **D1** · Files that name a changed file run in a second plain pytest call after the testmon selection, not as arguments to the testmon call. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: testmon given explicit files still deselects the unaffected tests among them, so a doc-only change would not run the tests that name the doc.
  Rejected: passing the files to the testmon call.
  See: https://github.com/marcozanger/arkimedes/pull/140 · report `runs/arkimedes/selective-gates-2/gate-select.report.md`
  </details>

- **D2** · The test count is read from the pytest summary line, and from the progress characters when there is none. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: pytest.ini adds `-q`, so a `-q` run becomes `-qq` and prints no summary line. The count is needed for the `ark-test: mode=<m> tests=<n> (<why>)` line.
  Rejected: overriding addopts.
  See: https://github.com/marcozanger/arkimedes/pull/140 · report `runs/arkimedes/selective-gates-2/gate-select.report.md`
  </details>

## gate-receipt — merged

- **D3** · The stop hook writes the gate receipt only if the tree is clean and the head is the same both before and after the gate. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: the receipt then vouches for exactly the committed head the gate tested.
  Rejected: checking only after the gate, which could vouch for a head whose tested tree had uncommitted edits.
  See: https://github.com/marcozanger/arkimedes/pull/141 · report `runs/arkimedes/selective-gates-2/gate-receipt.report.md`
  </details>

- **D4** · The runner checks for a reusable receipt in `_next_step`, before taking a gate slot. A receipt that is read is always removed, whether it matches or not. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: a reused gate needs no machine slot, and reading the receipt once means a stale receipt can never count twice.
  Rejected: checking inside `start_gate`, which would hold a gate slot for nothing.
  See: https://github.com/marcozanger/arkimedes/pull/141 · report `runs/arkimedes/selective-gates-2/gate-receipt.report.md`
  </details>

- **D5** · A reused gate is archived in `done/` as a gate receipt with start = end and exit 0, so the report shows a 0-minute gate step. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: `steps.py` builds its rows from `done/`.
  Rejected: writing steps.jsonl directly, since it is derived and not written by hand.
  See: https://github.com/marcozanger/arkimedes/pull/141 · report `runs/arkimedes/selective-gates-2/gate-receipt.report.md`
  </details>

- **D6** · The job escalated at the first attempt: task T5 asks for a reused gate's step to show `green (reused)`, but that label comes from `lib/ark/steps.py::_outcome`, which was outside the job's area. Marco widened the area to `lib/ark/steps.py` and approved the proposal, so the job resumed as attempt 2. · _arkimedes_
  <details><summary>more</summary>

  Why: the receipt and gate record already carried the outcome and `reused: "worker"`. Only the report label was missing, and a one-line change in `_outcome` plus a test in `tests/test_gate_receipt.py` closes it.
  Rejected: keeping the plain `green` label, which the gate record's `reused` field already covers but which does not meet T5.
  See: https://github.com/marcozanger/arkimedes/pull/141 · report `runs/arkimedes/selective-gates-2/gate-receipt.report.md`
  </details>

- **D7** · The first refresh against main@5364866e was green but stale, because the run branch had moved since the check, so the runner retried it. · _arkimedes_
  <details><summary>more</summary>

  Why: `ark/selective-gates-2` moved when gate-receipt merged. The check only counts for the branch state it ran on.
  Rejected: none. The run still closed with the retry pending.
  See: report `runs/arkimedes/selective-gates-2/gate-receipt.report.md`
  </details>
