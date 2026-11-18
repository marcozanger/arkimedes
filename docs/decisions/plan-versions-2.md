# Decisions — plan-versions-2

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/plan-versions-2/plan.md · approved 2026-11-16 · closed 2026-11-18
Jobs: 7 merged, 0 blocked · Decisions: 9 worker, 1 reviewer, 3 arkimedes

## versions-core — merged

- **D1** · `validate.desk_location` also accepts a snapshot path under the approval folder (`v<N>/plan.md`), instead of special-casing snapshots elsewhere. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: the T3 test validates the snapshot path, which otherwise fails the "plan is not on the desk" check.
  Rejected: skipping the location check for snapshots in `_check_frontmatter` (more special-casing).
  See: https://github.com/marcozanger/arkimedes/pull/104 · report `runs/arkimedes/plan-versions-2/versions-core.report.md`
  </details>

## versions-cli — merged

- **D2** · `test_change_table_per_job` builds jobs with `run.new_job` instead of the brief's bare `{"status": ...}` dicts. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: `run.save` renders `tier`, so the partial dicts raise KeyError.
  Rejected: changing `run.save` (outside the job's area).
  See: https://github.com/marcozanger/arkimedes/pull/109 · report `runs/arkimedes/plan-versions-2/versions-cli.report.md`
  </details>

- **D3** · The job gate went red three times in test files the job never touched. Euklides traced it to the base: the #103 load brake reads the machine's real load in tests, and `loop._load_wait` leaks between tests. Marco approved a `fix(base)` on the integration branch. Seven fix workers timed out. The eighth was told to touch only `tests/conftest.py` and run three test files. After that fix the gate passed. · _arkimedes_
  <details><summary>more</summary>

  Why: the cause sits in `tests/conftest.py`, outside the job's area, and affects every job in the run.
  Rejected: changing the job's own code (it was clean: only `cli.py`, `desk.py` and its test file).
  See: https://github.com/marcozanger/arkimedes/pull/109 · report `runs/arkimedes/plan-versions-2/versions-cli.report.md`
  </details>

## loop-adopt — merged

- **D4** · The worker ended FAILED because the same #103 load-brake leak held its tests' gates at `needs_gate`. Marco approved the shared base fix (q1). After seven timeouts of the fix worker, the eighth was given a minimal-scope instruction. The loop-adopt gate then re-ran on its existing head with no new worker. · _arkimedes_
  <details><summary>more</summary>

  Why: the leak predates the job and the fix is in `tests/conftest.py`, outside its area. Note that the run never sees main, so the same bug stays on main until it is landed there.
  Rejected: stopping the job (`:stop`).
  See: https://github.com/marcozanger/arkimedes/pull/110 · report `runs/arkimedes/plan-versions-2/loop-adopt.report.md`
  </details>

- **D5** · The base fix makes `os.getloadavg` return 0 in tests and clears `loop._load_wait`, instead of setting `load_max` to 0. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: `tests/test_paths.py` asserts the default `load_max` equals `default_load_max()`. The effect on the brake is the same, and `test_gate_brake`'s clock fixture still sets its own load.
  Rejected: setting `load_max` to 0 in the test limits.
  See: https://github.com/marcozanger/arkimedes/pull/110 · report `runs/arkimedes/plan-versions-2/loop-adopt.report.md`
  </details>

- **D6** · `LocalForge.close` still raises on a PR that is not open. `_drop_job_work` calls `view` first and closes only an OPEN PR. A refused version is told once per (version, reason) through the kept `_first_refusal`. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: the sweep tests rely on the raise, and `tests/test_ok_effects.py` (outside the area) tests `_first_refusal`.
  Rejected: the brief's silent no-op `close`, and one notice per version.
  See: https://github.com/marcozanger/arkimedes/pull/110 · report `runs/arkimedes/plan-versions-2/loop-adopt.report.md`
  </details>

- **D7** · When the run follows a legacy approval with no kept v1 text, `_adopt_version` marks every current job absent from the new manifest as `removed`. Before this, such a job stayed in `st["jobs"]` with no manifest entry. · _reviewer_
  <details><summary>more</summary>

  Why: `job_changes(None, new)` only listed the new jobs, so a job the first version dropped was never retired.
  Rejected: none stated. A regression test was added (`test_a_legacy_run_drops_a_job_its_first_version_removes`).
  Open: a version refused for overlap still leaves the approval file at the new sha, so `merge.plan_reason` holds the run. Fixing that needs a change outside this area.
  See: https://github.com/marcozanger/arkimedes/pull/110 · report `runs/arkimedes/plan-versions-2/loop-adopt.report.md`
  </details>

## followup-brief — merged

- **D8** · The diff in the follow-up brief uses a fence longer than any backtick run inside the diff, and control characters are stripped. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: `brief.py` has no general text sanitiser, and a diff containing ``` would end the block early.
  Rejected: the plain 3-backtick fence from the brief.
  See: https://github.com/marcozanger/arkimedes/pull/112 · report `runs/arkimedes/plan-versions-2/followup-brief.report.md`
  </details>

## report-versions — merged

- **D9** · Per-version cost buckets count process steps only, not wait-marco rows. The By PR Version column shows `s["version"]` for every job. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: waiting steps are not work, so totals match `s["usd"]`. The summary has no per-job merge time to derive a version from.
  Rejected: passing all rows, and deriving the version from step times.
  See: https://github.com/marcozanger/arkimedes/pull/113 · report `runs/arkimedes/plan-versions-2/report-versions.report.md`
  </details>

## ui-versions — merged

- **D10** · The versions payload takes `job_changes` only when both versions' plan files exist. The brief checked only the newer one. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: `job_changes` needs both paths.
  Rejected: calling it with a missing old plan.
  See: https://github.com/marcozanger/arkimedes/pull/115 · report `runs/arkimedes/plan-versions-2/ui-versions.report.md`
  </details>

- **D11** · The worker's full suite hit the Stop-hook time limit and never reported. Euklides judged the branch complete and clean and made no repair. The suite was 43% done after about 10 minutes, all passing. The job then went through its gate and review (CLEAN), the PR and the integration gate under load, and merged. · _arkimedes_
  <details><summary>more</summary>

  Why: the suite is slow, not hung; it is just longer than the worker's limit (840 to 1410 s). The gates waited on machine load (28.7, 21.8, 408.5 against the limit of 12), and GitHub connection resets briefly stalled the PR step.
  Rejected: treating the timeout as a failure and sending the job to a repair worker.
  See: https://github.com/marcozanger/arkimedes/pull/115 · report `runs/arkimedes/plan-versions-2/ui-versions.report.md`
  </details>

## docs — merged

- **D12** · The two new command rows (`ark replan`, `ark diff`) were also added to the ark-plan skill's own commands table. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: it mirrors CLAUDE.md.
  Rejected: leaving them out of the skill.
  See: https://github.com/marcozanger/arkimedes/pull/114 · report `runs/arkimedes/plan-versions-2/docs.report.md`
  </details>

- **D13** · After review, the "Where versions show" claims the code does not back (ark plans, report, history, control center) were removed from `docs/plan-versions.md`. The doc's version layout was aligned with `lib/ark/versions.py`. · _worker (model not recorded)_
  <details><summary>more</summary>

  Why: the docs must match what the code does.
  Rejected: keeping the broader claims.
  See: https://github.com/marcozanger/arkimedes/pull/114 · report `runs/arkimedes/plan-versions-2/docs.report.md`
  </details>
