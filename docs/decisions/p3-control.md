# Decisions — p3-control

Plan: /Users/marcozanger/.claude/arkimedes/desk/arkimedes/p3-control/plan.md · approved 2026-11-06 · closed 2026-11-07
Jobs: 5 merged, 0 blocked · Decisions: 6 worker, 0 reviewer, 2 arkimedes

## observe — merged

- **D1** · Implemented the brief's code verbatim, with no deviations from the plan. · _worker (T1)_
  <details><summary>más</summary>

  Why: the brief gave the full code and tests.
  Rejected: none.
  See: https://github.com/marcozanger/arkimedes/pull/4 · report `runs/arkimedes/p3-control/observe.report.md`
  </details>

## alerts — merged

No key decisions. The job merged on the first attempt, and the worker reported none.

## questions — merged

- **D2** · After three review rounds without CLEAN, the job was escalated and retried as a fourth attempt. The judge told the worker to check every path from state text to a file write, not only the one finding. · _arkimedes_
  <details><summary>más</summary>

  Why: a lone surrogate in a question text made `questions.save_file` raise `UnicodeEncodeError`. The error escaped `cycle` and lost every run's cleared alerts. Round 3 raised 3 findings, and the cap of 3 rounds without CLEAN was reached. The fourth attempt ended CLEAN.
  Rejected: failing the job. It was kept at the same tier (T2) with a targeted retry.
  See: https://github.com/marcozanger/arkimedes/pull/6 · report `runs/arkimedes/p3-control/questions.report.md`
  </details>

- **D3** · Surrogates are cleaned at three points the cycle cannot skip (`questions.render`, `_load`, before `notify.deliver`), plus `inbox.post`. The finding had preferred cleaning only in `render`. · _worker (T2)_
  <details><summary>más</summary>

  Why: the judge asked for every path from state text to a file write. `notify.record` raised out of `cycle` the same way, and `run.md` raised into "cannot be saved".
  Rejected: catching ValueError around `save_file`, which leaves `notify.record` open and an unwritable `questions.md` for good. Changing `notify.py` and `run.py`, which are outside the job's area.
  See: https://github.com/marcozanger/arkimedes/pull/6 · report `runs/arkimedes/p3-control/questions.report.md`
  </details>

- **D4** · `_load` leaves `repo` and `slug` uncleaned. · _worker (T2)_
  <details><summary>más</summary>

  Why: they name the run's folder, and a cleaned name would save the state somewhere else.
  Rejected: cleaning them too. As a result, a surrogate in an approval's `repo` or `slug` for a run with no state yet still reaches `run.md`, and `ark ctl._states` still reads unchecked state. Both are left as open issues.
  See: https://github.com/marcozanger/arkimedes/pull/6 · report `runs/arkimedes/p3-control/questions.report.md`
  </details>

## caps — merged

No key decisions. The job merged on the first attempt, and the worker reported none.

## intervene — merged

- **D5** · Corrected two assertions in the plan's `test_the_process_gets_twice_the_tier_time`: `2 * 60 * TIER_MINUTES["T1"] * 60` became `2 * TIER_MINUTES["T1"] * 60`, and likewise for `soft_deadline`. · _worker (T2)_
  <details><summary>más</summary>

  Why: the extra 60 expects 120 hours, while the plan's own implementation and `HARD_FACTOR = 2` give 2 hours.
  Rejected: changing the implementation to match the test, which would let a stuck worker run for 5 days.
  See: https://github.com/marcozanger/arkimedes/pull/8 · report `runs/arkimedes/p3-control/intervene.report.md`
  </details>

- **D6** · The plan's T14 and T15 tests now wait for the worker's files. `holding` waits until the fake worker has started, and a new `second_worker_got` helper replaces `attempts == 2` followed by reading `.argv`/`.prompt`. · _worker (T2)_
  <details><summary>más</summary>

  Why: the state counts an attempt when it starts the process, which writes its files a moment later, so the tests as written failed with FileNotFoundError.
  Rejected: changing the runner, since the race was only in the tests; `_anomaly` already tolerates a missing log.
  See: https://github.com/marcozanger/arkimedes/pull/8 · report `runs/arkimedes/p3-control/intervene.report.md`
  </details>

- **D7** · Two review rounds were needed before CLEAN. Round 1 found that a steer landing after the worker ended was dropped silently. Round 2 found that an adopted worker got no limits and that a tick the state missed could start a second one. · _arkimedes_
  <details><summary>más</summary>

  Why: the fixes were a "not delivered" alert from `on_worker`, and `reconcile` taking limits from the `.proc` start time through the shared `_watch_from` helper. `reconcile` also adopts a missed tick with `intervening = True`. `_model_unreachable` still drops `steering` silently. The worker left that as an open issue.
  Rejected: none recorded.
  See: https://github.com/marcozanger/arkimedes/pull/8 · report `runs/arkimedes/p3-control/intervene.report.md`
  </details>

## attach — merged

- **D8** · In `test_attach_takes_a_running_worker_and_the_gates_follow`, the test waits until the fake worker has written its `.prompt` (`worker_attempts("shell") == 1`) before attaching, not just until the status is `working`. · _worker (T2)_
  <details><summary>más</summary>

  Why: attach stopped the worker before the fake Python process wrote its prompt file, so the plan's final `worker_attempts == 1` check saw 0. The behaviour was right; only the test's timing was off.
  Rejected: changing the assertion to `<= 1`, which would no longer prove the worker ran exactly once.
  See: https://github.com/marcozanger/arkimedes/pull/9 · report `runs/arkimedes/p3-control/attach.report.md`
  </details>
