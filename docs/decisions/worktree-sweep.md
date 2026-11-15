# Decisions — worktree-sweep

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/worktree-sweep/plan.md · approved 2026-11-13 · closed 2026-11-15
Jobs: 3 merged, 0 blocked · Decisions: 5 worker, 0 reviewer, 2 arkimedes

## core — merged

- **D1** · `GhForge.close` runs `gh pr close <n> --comment <text>` instead of `--comment-file -`. The worker could not confirm the flag, because a hook blocked `gh` help. · _worker (T2)_
  <details><summary>more</summary>

  Why: As far as the worker knows, `gh pr close` takes `-c/--comment` and has no `--comment-file` flag. The comment is our own one-line text passed as an argv item, so no shell is involved. This was not verified against real `gh`.
  Rejected: `--comment-file -`, which probably fails on real gh. A separate `gh pr comment --body-file -` call before the close, which is two calls and can leave a comment on a PR that then fails to close.
  See: https://github.com/marcozanger/arkimedes/pull/94 · report `runs/arkimedes/worktree-sweep/core.report.md`
  </details>

- **D2** · The plan was silent on when to hold a branch, so the worker took the safer readings. A branch's local and origin copies are held together if its worktree is dirty, it is in use, or it has unpushed commits. "In use" means checked out in any worktree outside the ones this run removes, which is wider than the brief's "outside `ARK_WORKTREES`". A run whose `final_pr` is still open is left out of the plan, not listed as held. · _worker (T2)_
  <details><summary>more</summary>

  Why: These readings keep "after apply, the plan lists nothing" true. Items also carry an extra `branch` field, so a failure on a worktree or PR skips that branch's other items. A worktree is tied to `ark/<slug>-<folder name>`.
  Rejected: Holding a remote branch alone while deleting its local copy. Asking git for the branch at apply time, which does not work for detached scratch worktrees.
  See: https://github.com/marcozanger/arkimedes/pull/94 · report `runs/arkimedes/worktree-sweep/core.report.md`
  </details>

## cli — merged

- **D3** · A branch present both locally and on origin prints as one line `(local, origin)`, and the total counts distinct branch names. Worktree paths print as `wt/<path under the worktrees folder>`. · _worker (T1)_
  <details><summary>more</summary>

  Why: This matches the output example in the brief.
  Rejected: One line per ref. Absolute paths.
  See: https://github.com/marcozanger/arkimedes/pull/97 · report `runs/arkimedes/worktree-sweep/cli.report.md`
  </details>

- **D4** · The cli integration gate went red (exit 1) at 19:33. The runner sent the job back to the worker for attempt 2, and the worker made no code change. It judged the failing loop test (`test_a_save_that_fails_after_a_receipt_starts_nothing_in_that_cycle`) timing-flaky under load. The head stayed at 22acc88a and the job gate and review were green again. The integration gate was green on c4379256 at 20:15, then ran again because the branch had moved, and was green on eac07e7c before the merge at 20:40. · _arkimedes_
  <details><summary>more</summary>

  Why: The worker said the test passed 3 of 3 times alone, and the machine load was 14–24 against a limit of 12. It also had no full-suite result. The retry passing without a change fits flakiness, but this is not proven.
  Rejected: Editing a loop test outside this job's area.
  See: https://github.com/marcozanger/arkimedes/pull/97 · report `runs/arkimedes/worktree-sweep/cli.report.md`
  </details>

- **D5** · Main went red at cb441d47 (alert at 20:36), and refresh 1 had earlier failed on an integration gate timeout after 3600s. The run kept its base at c4379256 and did not merge cb441d47. · _arkimedes_
  <details><summary>more</summary>

  Why: The failing tests (`test_loop.py` and `test_self_recovery_fixes.py`) are in main, not in this run's jobs. The last event lines show the run closing without cb441d47, with the base at c4379256 and the cli merge gate green on eac07e7c.
  Rejected: Merging a red main into the run branch.
  See: https://github.com/marcozanger/arkimedes/pull/97 · report `runs/arkimedes/worktree-sweep/cli.report.md`
  </details>

## loop — merged

- **D6** · `sweep.daily` writes the day to `runs/sweep.json` before it starts sweeping, keeping the old held and error records, then writes the full record at the end. · _worker (T2)_
  <details><summary>more</summary>

  Why: A sweep that raises then alerts once and is not retried until the next day, so it cannot send the same alert every cycle.
  Rejected: Stamping the day only after success, which repeats the exception alert every cycle all day.
  See: https://github.com/marcozanger/arkimedes/pull/98 · report `runs/arkimedes/worktree-sweep/loop.report.md`
  </details>

- **D7** · The `problems` list from `sweep.plan` (skipped repos or states) raises no loop alert. The date in "abandoned on <date>" is the day the run became abandoned (`closed_at + abandon_days`), not the close date. · _worker (T2)_
  <details><summary>more</summary>

  Why: The plan names only held and error alerts, and `ark sweep --dry-run` shows the problems. The date is the `since` value that `sweep.abandoned` returns, and it matches the "abandoned since" wording the sweep uses.
  Rejected: A new alert format the plan does not define. The close date.
  See: https://github.com/marcozanger/arkimedes/pull/98 · report `runs/arkimedes/worktree-sweep/loop.report.md`
  </details>
