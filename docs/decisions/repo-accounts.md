# Decisions — repo-accounts

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/repo-accounts/plan.md · approved 2026-11-07 · closed 2026-11-08
Jobs: 2 merged, 0 blocked · Decisions: 0 worker, 0 reviewer, 2 arkimedes

## repo-depth — merged

- **D1** · The integrate step timed out and `gh pr view 19` then failed to reach api.github.com. The run kept retrying and did not block the job. PR #19 merged into ark/repo-accounts about 1h54m after the timeout. · _arkimedes_
  <details><summary>more</summary>

  Why: The review was CLEAN and the job gate was green, so the failure was in the forge connection, not the work. The events show no human intervention.
  Rejected: Blocking the job on the first forge error, which would have discarded a green, reviewed PR over a network problem.
  See: https://github.com/marcozanger/arkimedes/pull/19 · report `runs/arkimedes/repo-accounts/repo-depth.report.md`
  </details>

## accounts — merged

- **D2** · After the review came back CLEAN, `gh pr view 22` kept failing with the same GitHub connection error. The run raised an ALERT after 20 failed cycles (about 9h) and still did not block the job. PR #22 merged about 13h after the first error. · _arkimedes_
  <details><summary>more</summary>

  Why: The code was reviewed and the gate was green, and the error was the same transient connection failure as in repo-depth. Retrying was cheaper than discarding the work. The job depends on `GH_TOKEN` handling in `GhForge._gh`, but the events do not tie the failure to that change. The report lists no open issues.
  Rejected: Blocking or escalating the job once the alert fired. The events do not show why the run kept retrying instead, so a reviewer may want to check whether the outage was on GitHub's side or in this change's token handling.
  See: https://github.com/marcozanger/arkimedes/pull/22 · report `runs/arkimedes/repo-accounts/accounts.report.md`
  </details>

## Daily cap per account

An account rule may carry an optional `usd_per_day`, a positive number: that account's own daily cap. A bad value is an
`AccountError`. The personal account (repos no rule matches) keeps `limits.usd_per_day`. Every spend line names its
account; an older line without one counts for its run's repo.
