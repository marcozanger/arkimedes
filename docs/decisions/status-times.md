# Decisions — status-times

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/status-times/plan.md · approved 2026-11-08 · closed 2026-11-07
Jobs: 1 merged, 0 blocked · Decisions: 2 worker, 0 reviewer, 0 arkimedes

## status-times — merged

- **D1** · A feature in `waiting` counts as live, like `running`: its elapsed time keeps counting up to now and its eta is shown. · _worker (Sonnet 5.5)_
  <details><summary>más</summary>

  Why: The brief says eta is shown only while the feature is running or waiting.
  Rejected: Freezing elapsed for `waiting`.
  See: https://github.com/marcozanger/arkimedes/pull/21 · report `runs/arkimedes/status-times/status-times.report.md`
  </details>

- **D2** · If the feature is live but its plan file cannot be loaded, eta is shown as `?`. · _worker (Sonnet 5.5)_
  <details><summary>más</summary>

  Why: Without the plan the budget is unknown, so no eta can be computed. `?` says the value is unknown.
  Rejected: Showing `-`, which would look like "not applicable".
  See: https://github.com/marcozanger/arkimedes/pull/21 · report `runs/arkimedes/status-times/status-times.report.md`
  </details>
