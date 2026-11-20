# Decisions — jira-intake

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/jira-intake/plan.md · approved 2026-11-16 · closed 2026-11-20
Jobs: 0 merged, 2 blocked · Decisions: 0 worker, 0 reviewer, 5 arkimedes

## run — refresh of the base against main

- **D1** · Refresh 0 (main@b828bb69) was skipped on Marco's answer `skip`, so the run's base stayed where it was. The refresh worker had joined main and the base, but the integration gate stayed red on its result. · _arkimedes_
  <details><summary>more</summary>

  Why: The gate failed with exit 1 on `tests/test_merge.py::test_gh_merged_reads_as_merged_however_the_call_ended_and_a_rerun_does_not_merge_again[merged_ok]`. The error was `ForgeError: gh pr view 1: timed out after 1s`. The log shows a gate waiting on machine load (24.5 against a limit of 12), but it does not say whether load caused the timeout. The failure was not resolved.
  Rejected: `retry` (another refresh worker with the same $3.00 budget).
  See: no PR · alert q1
  </details>

- **D2** · For refresh 1 (main@d0fc9fa7, gate red on the merge), Marco answered `retry` after the first refresh worker died without a receipt. This started a second worker with the same $3.00 budget, and the extra spend was Marco's call. · _arkimedes_
  <details><summary>more</summary>

  Why: The first worker ended without finishing, so the base was still not refreshed.
  Rejected: `skip`.
  See: no PR · alert q2
  </details>

- **D3** · After the retry worker timed out (over its time limit), Marco answered `skip` and refresh 1 was dropped. The base stayed until main moves again. · _arkimedes_
  <details><summary>more</summary>

  Why: Marco chose not to spend on a third attempt after two failed workers (one died, one timed out). The log gives no further reason.
  Rejected: another `retry`.
  See: no PR · alert q3
  </details>

## jira-resolver — blocked

- **D4** · The job was stopped by Marco and closed as blocked, with no PR and no report. · _arkimedes_
  <details><summary>more</summary>

  Why: The block reason is only "stopped by Marco", at 2026-11-20T15:49:27. The run does not record why. By then the base had not been refreshed since main moved (D1, D3).
  Rejected: none recorded.
  See: no PR · report `runs/arkimedes/jira-intake/jira-resolver.report.md` (none was written)
  </details>

## jira-skill — blocked

- **D5** · The job was stopped by Marco and closed as blocked, with no PR and no report, at the same moment as jira-resolver. · _arkimedes_
  <details><summary>more</summary>

  Why: The block reason is only "stopped by Marco". Both jobs were stopped in the same second, so this was probably a single decision to close the run. That is an inference. The run does not say why.
  Rejected: none recorded.
  See: no PR · report `runs/arkimedes/jira-intake/jira-skill.report.md` (none was written)
  </details>
