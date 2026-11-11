# Decisions — control-center-ui

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/control-center-ui/plan.md · approved 2026-11-11 · closed 2026-11-11
Jobs: 2 merged, 0 blocked · Decisions: 4 worker, 0 reviewer, 3 arkimedes

## server — merged

- **D1** · The server job's gate went red on `tests/test_english_only.py` because the run's decision log (`docs/decisions/control-center.md:52`, D6) quotes `'más'`. The job stalled in three questions (q1–q3) and ended with the fix landing on main (#53), outside the job's area. The run base was then moved forward. · _arkimedes_
  <details><summary>more</summary>

  Why: The file is outside the server job's area and the hook blocks the worker from editing it. Widening the area or editing it from the worker would have broken the area rule.
  Rejected: (q1) Allow-listing the line in `tests/test_english_only.py`, which Marco approved at first ("ok your proposal"). (q1) Letting the gate stay red on this one test. (q2) Widening the job's area to include the file.
  See: PR https://github.com/marcozanger/arkimedes/pull/54 · report `runs/arkimedes/control-center-ui/server.report.md`
  </details>

- **D2** · Marco switched from the allow-list proposal (q1) to rewording D6 without the Spanish word. In q2 he delegated the edit to another agent, and the fix landed on main as #53. In q3 he told the worker to rebase on the base and re-run only its gate, with no new worker attempt. · _arkimedes_
  <details><summary>more</summary>

  Why: Rewording removes the cause. The allow-list only hides it. A gate-only re-run avoids new worker changes for a problem outside the job's code.
  Rejected: A new worker attempt. Leaving D6 as it was with an allow-list entry.
  See: PR https://github.com/marcozanger/arkimedes/pull/54 · report `runs/arkimedes/control-center-ui/server.report.md`
  </details>

- **D3** · The worker brought the fixed base in with a merge commit, not a rebase, although Marco's q3 answer said "rebase". · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: Worker rules forbid rewriting commits that may already be pushed.
  Rejected: Rebase.
  See: PR https://github.com/marcozanger/arkimedes/pull/54 · report `runs/arkimedes/control-center-ui/server.report.md`
  </details>

- **D4** · When several history rows share the same repo and plan, the cost block uses the newest one. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: A re-run closes later, so the newest row reflects the latest run.
  Rejected: The first matching row.
  See: PR https://github.com/marcozanger/arkimedes/pull/54 · report `runs/arkimedes/control-center-ui/server.report.md`
  </details>

## page — merged

- **D5** · The URL-hash key is the whole text before the last known tab name, matched against plan keys. It is not split into repo and slug. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: Slugs can contain "/" (the sample has `mobile/onboarding-copy`), so a split is ambiguous.
  Rejected: Splitting on the last two segments.
  See: PR https://github.com/marcozanger/arkimedes/pull/55 · report `runs/arkimedes/control-center-ui/page.report.md`
  </details>

- **D6** · The notification logic (T3) and the tab-title glue (T4) went into one commit instead of two. · _worker (Sonnet 5.5)_
  <details><summary>more</summary>

  Why: Both edit `ui/index.html` and the tests together, so they can't be separated cleanly.
  Rejected: Two commits.
  See: PR https://github.com/marcozanger/arkimedes/pull/55 · report `runs/arkimedes/control-center-ui/page.report.md`
  </details>

- **D7** · The worker fixed `ui/sample.json` to match the real snapshot keys, which the plan did not list. · _arkimedes_
  <details><summary>more</summary>

  Why: The page reads the real snapshot, and the sample keys had drifted from it. The worker reported this in its summary only, not in DECISIONS, so a reviewer should confirm it was intended.
  Rejected: Leaving the sample as it was.
  See: PR https://github.com/marcozanger/arkimedes/pull/55 · report `runs/arkimedes/control-center-ui/page.report.md`
  </details>
