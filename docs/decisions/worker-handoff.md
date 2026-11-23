# Decisions — worker-handoff

Plan: /Users/marcozanger/.claude/arkimedes/approvals/arkimedes/worker-handoff/v1/plan.md · approved 2026-11-23 · closed 2026-11-23
Jobs: 2 merged, 0 blocked · Decisions: 3 worker, 0 reviewer, 0 arkimedes

## handoff — merged

- **D1** · The `FAKE_NOREPORT` test double commits, skips the report and ends with `STATUS: FAILED`, rather than crashing with no result line. · _worker (model not stated)_
  <details><summary>more</summary>

  Why: a FAILED end routes straight to Euklides and its `escalate`, which is the path the test needs. A dead process would go through the infra-retry handling instead.
  Rejected: exit 1 with no result line.
  See: https://github.com/marcozanger/arkimedes/pull/137 · report `runs/arkimedes/worker-handoff/handoff.report.md`
  </details>

- **D2** · A report that is only whitespace counts as no report, so the handoff says the previous worker left none. · _worker (model not stated)_
  <details><summary>more</summary>

  Why: an empty file gives the next worker nothing to use.
  Rejected: fencing an empty block.
  See: https://github.com/marcozanger/arkimedes/pull/137 · report `runs/arkimedes/worker-handoff/handoff.report.md`
  </details>

## evidence — merged

- **D3** · The done-gate hook counts the report's evidence bullets only after the gate is green, as the plan says. · _worker (model not stated)_
  <details><summary>more</summary>

  Why: a red gate already blocks, so counting earlier adds nothing.
  Rejected: counting before the gate.
  See: https://github.com/marcozanger/arkimedes/pull/138 · report `runs/arkimedes/worker-handoff/evidence.report.md`
  </details>
