# Decisions — account-caps

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/account-caps/plan.md · approved 2026-11-10 · closed 2026-11-11
Jobs: 3 merged, 0 blocked · Decisions: 5 worker, 0 reviewer, 2 arkimedes

## caps-core — merged

- **D1** · `check_limits` calls the private `accounts._rules()` instead of a new public accessor. · _worker (T1)_
  <details><summary>more</summary>

  Why: the brief allows either, and the private call is what the brief's own code does.
  Rejected: renaming it to a public `rules`, which would touch more lines.
  See: https://github.com/marcozanger/arkimedes/pull/52 · report `runs/arkimedes/account-caps/caps-core.report.md`
  </details>

- **D2** · The integration gate went red because `tests/test_english_only.py` failed on `docs/decisions/control-center.md:52` ('más'). That file came from #51 and is outside caps-core's area. Marco's answer (q1) was to fix it on main (#53) and re-run only the gate, with no new worker attempt. · _arkimedes_
  <details><summary>more</summary>

  Why: the worker correctly refused to edit a file outside its area, and the failure would also have hit loop-per-account.
  Rejected: letting the worker edit the docs file, and adding an allowlist entry to the English-only test (Euklides's alternative proposal).
  See: https://github.com/marcozanger/arkimedes/pull/52 · report `runs/arkimedes/account-caps/caps-core.report.md`
  </details>

- **D3** · The worker's attempt 3 merged `origin/main` into its job branch to pick up #53, which put `docs/decisions/control-center.md` into its diff. That was flagged as an out-of-area change (q2). Marco allowed `ark/account-caps` to take main, and the job to be rebuilt as `ark/account-caps` plus commit 9e6ecda only, with no main merge. · _arkimedes_
  <details><summary>more</summary>

  Why: the diff had to stay on the four area files, and the integration branch needed #53 for the English-only test to pass.
  Rejected: keeping the main merge in the job branch, and treating the answer's wording about "control-center-ui" as the base to rebase on, since `origin/ark/account-caps` still lacked #53.
  See: https://github.com/marcozanger/arkimedes/pull/52 · report `runs/arkimedes/account-caps/caps-core.report.md`
  </details>

## loop-per-account — merged

- **D4** · `_nothing_to_advance` treats a falsy `capped_accounts` as empty. · _worker (T2)_
  <details><summary>more</summary>

  Why: `tests/test_control_alerts.py` is outside the job's area and still calls `_flag_stuck(states, False, ...)`.
  Rejected: editing that test file.
  See: https://github.com/marcozanger/arkimedes/pull/57 · report `runs/arkimedes/account-caps/loop-per-account.report.md`
  </details>

- **D5** · The new tests put the demo repo under a `demo` account rule instead of building a second repo. · _worker (T2)_
  <details><summary>more</summary>

  Why: the brief allows it, and the `feature` fixture drives one repo per helper call.
  Rejected: a two-repo setup.
  See: https://github.com/marcozanger/arkimedes/pull/57 · report `runs/arkimedes/account-caps/loop-per-account.report.md`
  </details>

## cap-question — merged

- **D6** · The question alert reads `<account>: daily spending cap reached · question cN: … — \`ark answer\`` instead of the plan's `question cN for account <account>: …`. · _worker (T2)_
  <details><summary>more</summary>

  Why: `test_control_caps.py` is in the gate but outside the area, and waits for "personal: daily spending cap", so that phrase has to stay.
  Rejected: editing `test_control_caps.py`.
  See: https://github.com/marcozanger/arkimedes/pull/58 · report `runs/arkimedes/account-caps/cap-question.report.md`
  </details>

- **D7** · The existing `test_account_caps_loop` test keeps its "daily spending cap" assertions rather than being rewritten around the question id. · _worker (T2)_
  <details><summary>more</summary>

  Why: the new alert still contains that text exactly once, so the test still checks one alert per account.
  Rejected: rewriting it around the question id, since the new tests already cover the question.
  See: https://github.com/marcozanger/arkimedes/pull/58 · report `runs/arkimedes/account-caps/cap-question.report.md`
  </details>
