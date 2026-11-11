from datetime import datetime, timedelta, timezone

from ark import timing

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def at(m):
    return (T0 + timedelta(minutes=m)).isoformat()


def mins(m):
    return T0 + timedelta(minutes=m)


def J(id, minutes, status="pending", after=(), started=None, finished=None):
    return {"id": id, "minutes": minutes, "status": status, "after": list(after), "started": started, "finished": finished}


def test_nothing_started_forecast_equals_estimate():
    t = timing.compute([J("a", 30), J("b", 20, after=["a"]), J("c", 10)], "waiting", T0)
    assert t["estimated_end"] == mins(50) == t["forecast_end"]
    assert (t["delay_min"], t["left_min"]) == (0, 50)
    assert t["jobs"]["b"]["plan_start"] == mins(30) and t["jobs"]["c"]["plan_end"] == mins(10)


def test_a_job_merged_early_pulls_the_end_in():
    jobs = [J("a", 30, "merged", started=at(0), finished=at(20)), J("b", 20, after=["a"])]
    t = timing.compute(jobs, "running", mins(20))
    assert (t["delay_min"], t["left_min"]) == (-10, 20)


def test_a_job_past_its_budget_pushes_its_dependents():
    jobs = [J("a", 30, "working", started=at(0)), J("b", 20, after=["a"])]
    t = timing.compute(jobs, "running", mins(45))
    assert t["jobs"]["a"]["fc_end"] == mins(45) and t["jobs"]["b"]["fc_start"] == mins(45)
    assert (t["delay_min"], t["left_min"]) == (15, 20)


def test_a_parallel_job_over_budget_does_not_delay_the_plan_if_the_other_is_longer():
    jobs = [J("a", 10, "working", started=at(0)), J("b", 60, "working", started=at(0))]
    t = timing.compute(jobs, "running", mins(20))
    assert t["delay_min"] == 0 and t["left_min"] == 40


def test_a_blocked_job_leaves_time_left_unknown():
    jobs = [J("a", 30, "blocked", started=at(0), finished=at(40)), J("b", 20, after=["a"])]
    t = timing.compute(jobs, "running", mins(50))
    assert t["left_min"] is None and t["delay_min"] is not None


def test_a_held_run_has_no_time_left():
    t = timing.compute([J("a", 30, "working", started=at(0))], "held", mins(5))
    assert t["left_min"] is None


def test_a_done_run_has_no_time_left_and_ends_at_its_real_end():
    t = timing.compute([J("a", 30, "merged", started=at(0), finished=at(25))], "done", mins(300))
    assert t["left_min"] == 0 and t["forecast_end"] == mins(25) and t["delay_min"] == -5


def test_a_merged_job_without_times_uses_its_estimate():
    jobs = [J("a", 30, "merged"), J("b", 20, "working", after=["a"], started=at(30))]
    t = timing.compute(jobs, "running", mins(35))
    assert t["forecast_end"] is not None and t["jobs"]["a"]["fc_end"] == t["jobs"]["a"]["plan_end"]


def test_bad_or_naive_times_count_as_missing():
    t = timing.compute([J("a", 30, "working", started="yesterday"), J("b", 5, "working", started="2026-11-07T12:00:00")],
                       "running", T0)
    assert t["jobs"]["a"]["fc_start"] == T0 and t["jobs"]["b"]["fc_start"] == T0


def test_an_after_that_names_no_job_is_ignored():
    t = timing.compute([J("a", 30, after=["ghost"])], "waiting", T0)
    assert t["estimated_end"] == mins(30)


def test_no_jobs():
    t = timing.compute([], "waiting", T0)
    assert t["estimated_end"] is None and t["forecast_end"] is None
    assert t["delay_min"] is None and t["left_min"] is None and t["jobs"] == {}
