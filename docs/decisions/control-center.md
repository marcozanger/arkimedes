# Decisions — control-center

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/control-center/plan.md · approved 2026-11-09 · closed 2026-11-11
Jobs: 3 merged, 1 blocked · Decisions: 4 worker, 0 reviewer, 3 arkimedes

## runner-feed — merged

- **D1** · The worker merged `origin/ark/control-center` into its branch instead of rebasing, even though the note asked for a rebase. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: Worker rules forbid rewriting history, and the runner may already have pushed the branch.
  Rejected: Rebase.
  See: https://github.com/marcozanger/arkimedes/pull/47 · report `runs/arkimedes/control-center/runner-feed.report.md`
  </details>

- **D2** · The integration gate failed repeatedly, and the worker asked to widen its area to `tests/test_sleep_retry.py`. The job was $20.64 over a $6 budget. Marco refused the widening. His session fixed the stale test on the base instead (now 709c2e7). The worker merged the base and resolved the two `lib/ark/loop.py` conflicts with #45 by keeping both behaviours. · _arkimedes_
  <details><summary>more</summary>

  Why: The failures came from the base, not from this job: the stale `needs_judge`/`judge` test names, plus a content conflict with #45's closed-lid handling. Marco approved up to $4 extra for the rebase and told the worker to end BLOCKED if the conflict could not be resolved without changing #45's behaviour. It did not need to.
  Rejected: Widening the job's area to the test file (q26); editing `docs/decisions` (q23), since the base already had #48's fix.
  See: https://github.com/marcozanger/arkimedes/pull/47 · report `runs/arkimedes/control-center/runner-feed.report.md`
  </details>

## runs-json — merged

- **D3** · After the integration gate failed once, the worker translated the Spanish help and error strings in `monitor.py` to English. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: `test_english_only` forbids Spanish in tracked files, and the brief's strings were Spanish.
  Rejected: Allow-listing the file in the test.
  See: https://github.com/marcozanger/arkimedes/pull/50 · report `runs/arkimedes/control-center/runs-json.report.md`
  </details>

## page — merged

- **D4** · `app/sample.json` follows the fields the page itself reads, not the T5 schema the brief said to copy key for key. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: The T5 schema lives in another job and is not in this worktree.
  Rejected: Waiting for T5. The `shell` job may need to align key names. Comparing the sample's keys with `ark runs --json` and the Safari light/dark check (Step 6) are still pending.
  See: https://github.com/marcozanger/arkimedes/pull/46 · report `runs/arkimedes/control-center/page.report.md`
  </details>

- **D5** · The worker read Marco's "y" on the `sleep_retry` failure as "fix it here" and committed e1c7858 (`lib/ark/loop.py`, `tests/test_sleep_retry.py`), outside its area. Later, with the fix already on the base, it reverted that commit (7ab7986) and merged the base, because it may not reset or rebase. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: The area check flagged the files, and the same fix was already on the base (709c2e7). After the revert and merge, the branch diff was only `app/index.html`, `app/sample.json` and `tests/test_app_page.py`.
  Rejected: Leaving the fix on the branch (widening the area); reset or rebase (forbidden by worker rules).
  See: https://github.com/marcozanger/arkimedes/pull/46 · report `runs/arkimedes/control-center/page.report.md`
  </details>

- **D6** · `page` was blocked many times by the "changed files outside its area" check, and it ran up to about $44 of a $5 budget over 24 attempts. The check flagged docs fixes (a Spanish word in a summary label), main's own commits, and the `sleep_retry` fix. Marco resolved it by landing the fixes on main (#48) and moving the run's base `ark/control-center` forward to main, and the branch was rebuilt as origin/main + b225ddb. The run budget was raised four times (+10, +10, +25, +10). · _arkimedes_
  <details><summary>more</summary>

  Why: The failures were not in the page itself. The base had stale docs and tests, and the worker kept applying and reverting the same out-of-area fix after contradictory answers. Moving the base let the area check pass against it. The fixes were also needed in `main` itself, so Marco landed them there, and Marco's session opened the sleep-retry PR to main.
  Rejected: Widening `page`'s area in the plan and re-approving it (offered several times); making the docs fix part of this job.
  See: https://github.com/marcozanger/arkimedes/pull/46 · report `runs/arkimedes/control-center/page.report.md`
  </details>

## shell — blocked

- **D7** · `shell` never started and is blocked with no PR and no report, because a job it depends on (`page`) was blocked when the dependency check ran. · _arkimedes_
  <details><summary>more</summary>

  Why: The scheduler stopped it on "a job it depends on is blocked". `page` was merged only afterwards. The job stays blocked in this run and needs a new plan or run. Before it starts, someone must do the Safari check (Step 6) and compare the keys of `sample.json` with `ark runs --json`.
  Rejected: None recorded.
  See: no PR · report `runs/arkimedes/control-center/shell.report.md` (no report)
  </details>
