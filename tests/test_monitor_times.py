from control_helpers import drive, settled, state

from ark import run


def test_stamp_times_rules(monkeypatch):
    monkeypatch.setattr(run, "now", lambda: "T")
    st = {"jobs": {
        "a": {"status": "pending"},
        "b": {"status": "working"},
        "c": {"status": "merged", "started": "S"},
        "d": {"status": "needs_worker", "started": "S", "finished": "F"},
        "e": {"status": "needs_worker"},
    }}
    run.stamp_times(st)
    j = st["jobs"]
    assert "started" not in j["a"] and "finished" not in j["a"]
    assert j["b"] == {"status": "working", "started": "T"}
    assert j["c"] == {"status": "merged", "started": "S", "finished": "T"}
    assert j["d"] == {"status": "needs_worker", "started": "S"}      # left blocked: keeps its start
    assert "started" not in j["e"]


def test_stamping_again_keeps_the_first_times(monkeypatch):
    st = {"jobs": {"a": {"status": "merged"}}}
    monkeypatch.setattr(run, "now", lambda: "first")
    run.stamp_times(st)
    monkeypatch.setattr(run, "now", lambda: "second")
    run.stamp_times(st)
    assert st["jobs"]["a"] == {"status": "merged", "started": "first", "finished": "first"}


def test_waiting_on_a_dependency_is_not_started(monkeypatch):
    monkeypatch.setattr(run, "now", lambda: "T")
    st = {"jobs": {
        "a": {"status": "waiting_dep"},
        "b": {"status": "pending", "started": "S", "attempts": 0},     # stamped by older code while it waited
        "c": {"status": "waiting_dep", "started": "S", "attempts": 2},  # really ran before: keeps its start
    }}
    run.stamp_times(st)
    j = st["jobs"]
    assert "started" not in j["a"] and "started" not in j["b"]
    assert j["c"]["started"] == "S"


def test_the_monitor_ignores_a_false_start_on_a_job_that_never_ran():
    from ark import monitor
    never = monitor._job("x", {}, {"status": "pending", "started": "S", "attempts": 0}, {})
    ran = monitor._job("y", {}, {"status": "working", "started": "S", "attempts": 1}, {})
    assert never["started"] is None and ran["started"] == "S"


def test_a_finished_run_has_start_and_end_on_every_job(feature):
    feature()
    drive(settled)
    for j in state()["jobs"].values():
        assert j["started"] <= j["finished"]
