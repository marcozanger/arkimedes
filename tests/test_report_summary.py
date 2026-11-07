from report_helpers import iso, job, mjobs, receipt, result_line, run_state


def summarize(st):
    from ark import steps, summary
    return summary.summarize(st, steps.build(st), mjobs())


def test_working_time_is_the_union_of_what_ran(ark_home):
    receipt("shell.worker", 0, 10, usd=1.0, log=result_line(usd=1.0))
    receipt("ui.worker", 5, 15, usd=0.5, log=result_line(usd=0.5))       # in parallel: 15 min of work, not 20
    receipt("shell.gate", 20, 22, usd=None, log="ok\n")
    s = summarize(run_state())
    assert s["working_minutes"] == 17.0 and s["wall_minutes"] == 60.0
    assert s["usd"] == 1.5 and s["tokens"] == 2 * 610
    assert (s["merged"], s["blocked"]) == (2, 0)
    assert (s["budget_usd"], s["budget_minutes"]) == (5, 50)


def test_waiting_for_marco_and_per_job(ark_home):
    receipt("shell.worker", 0, 10, result="STATUS: FAILED x", usd=1.0, log=result_line(usd=1.0))
    receipt("shell.worker", 11, 20, usd=2.0, log=result_line(model="claude-opus-5-5", usd=2.0))
    receipt("shell.review", 21, 22, result='{"verdict": "CLEAN", "findings": []}', usd=0.1, log=result_line(usd=0.1))
    receipt("shell.gate", 23, 24, exit=1, usd=None, log="")
    st = run_state(jobs={"shell": job("blocked", tier_path=["T1", "T2"], steers=1, integrate_fails=2,
                                      reason="job gate red (exit 1): <boom>")},
                   answers=[{"id": "q1", "kind": "worker", "job": "shell", "question": "?", "answer": "!",
                             "asked": iso(30), "at": iso(42)}])
    s = summarize(st)
    shell = s["jobs"]["shell"]
    assert s["waiting_minutes"] == 12.0 and s["integrate_fails"] == 2
    assert (shell["attempts"], shell["reviews"], shell["gate_fails"], shell["steers"]) == (2, 1, 1, 1)
    assert shell["tiers"] == ["T1", "T2"] and shell["usd"] == 3.1 and shell["minutes"] == 21.0
    assert (shell["budget_usd"], shell["budget_minutes"]) == (3, 30)
    assert shell["reason"] == "job gate red (exit 1): <boom>"    # escaped by the renderer, not here
    assert s["by_kind"]["worker"]["n"] == 2 and s["by_kind"]["worker"]["usd"] == 3.0
    assert set(s["by_model"]) == {"sonnet", "opus", "script"}


def test_a_missing_number_marks_the_summary_estimated(ark_home):
    receipt("shell.worker", 0, 10, usd=None, log="")
    s = summarize(run_state())
    assert s["estimated"] is True and s["usd"] == 0


def test_a_plan_without_budgets_is_fine(ark_home):
    from ark import steps, summary
    st = run_state()
    s = summary.summarize(st, steps.build(st), {})
    assert s["budget_usd"] is None and s["jobs"]["shell"]["budget_usd"] is None
