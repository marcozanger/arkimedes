import pytest
from control_helpers import ark, change_state, drive, job_status, settled, state

from ark import inbox, loop, run


def test_retry_refuses_a_job_that_is_not_blocked(feature, capsys):
    feature()
    loop.cycle()
    code, out = ark(capsys, "retry", "shell")
    assert code == 1 and "not blocked" in out


def test_retry_puts_a_blocked_job_back_on_its_step(feature, capsys):
    feature()
    loop.cycle()
    change_state(shell={"status": "blocked", "reason": "stopped by Marco", "blocked_from": "needs_gate",
                        "head": "a" * 40, "step_tries": 3, "euklides_count": 4})
    code, out = ark(capsys, "retry", "shell")
    assert "next cycle" in out
    loop.cycle()
    j = state()["jobs"]["shell"]
    assert j["status"] != "blocked"
    assert not j.get("reason")
    assert "step_tries" not in j and j.get("euklides_count", 0) == 0


@pytest.mark.parametrize("was,now", [("working", "needs_worker"), ("gating", "needs_gate"),
                                     ("reviewing", "needs_review"), ("integrating", "needs_integrate"),
                                     ("needs_merge", "needs_integrate"), ("needs_push", "needs_push"),
                                     ("waiting_dep", "pending"), ("waiting_answer", "needs_worker")])
def test_the_step_comes_from_where_it_was_blocked(was, now):
    j = {"status": "blocked", "blocked_from": was, "head": "a" * 40}
    assert loop._retry_status(j) == now


def test_a_job_blocked_by_older_code_goes_back_to_the_worker_or_the_push():
    assert loop._retry_status({"status": "blocked", "head": None}) == "needs_worker"
    assert loop._retry_status({"status": "blocked", "head": "a" * 40}) == "needs_push"


def test_retry_releases_blocked_dependents(feature, capsys):
    feature()
    loop.cycle()
    change_state(shell={"status": "blocked", "reason": "x", "blocked_from": "working"},
                 ui={"status": "blocked", "reason": "a job it depends on is blocked: shell", "waiting_on": "shell"})
    ark(capsys, "retry", "shell")
    loop.cycle()
    assert state()["jobs"]["ui"]["status"] in ("pending", "waiting_dep")


def test_retry_reopens_a_run_that_closed_blocked(feature, capsys):
    feature()
    loop.cycle()
    change_state(shell={"status": "blocked", "reason": "x", "blocked_from": "working"},
                 ui={"status": "blocked", "reason": "a job it depends on is blocked: shell", "waiting_on": "shell"})
    st = state()
    st.update(status="blocked", closing="done", decisions_sha="b" * 40)
    run.save(st)
    ark(capsys, "retry", "demo/notes-edit:shell")
    loop.cycle()
    st = state()
    assert st["status"] == "running" and st["closing"] is None and "decisions_sha" not in st
    drive(settled)
    assert job_status("shell") == "merged" and job_status("ui") == "merged"


def test_a_runner_process_cannot_retry(feature, capsys, monkeypatch):
    feature()
    monkeypatch.setenv("ARK_ROLE", "worker")
    code, out = ark(capsys, "retry", "shell")
    assert code == 1 and "runner process" in out
    assert not inbox.pending("demo", "notes-edit")


def _closed_days_ago(days):
    from datetime import datetime, timedelta, timezone
    change_state(shell={"status": "blocked", "reason": "x", "blocked_from": "working"},
                 ui={"status": "blocked", "reason": "a job it depends on is blocked: shell", "waiting_on": "shell"})
    st = state()
    at = datetime.now(timezone.utc) - timedelta(days=days)
    st.update(status="blocked", closing="done", closed_at=at.isoformat())
    run.save(st)
    return (at + timedelta(days=14)).date().isoformat()


def test_retry_refuses_a_run_abandoned_15_days_ago(feature, capsys):
    feature()
    loop.cycle()
    since = _closed_days_ago(15)
    code, out = ark(capsys, "retry", "demo/notes-edit:shell")
    assert code == 1
    assert f"demo/notes-edit was abandoned on {since}: plan it again" in out
    assert not inbox.pending("demo", "notes-edit")


def test_retry_of_a_run_closed_3_days_ago_works(feature, capsys):
    feature()
    loop.cycle()
    _closed_days_ago(3)
    code, out = ark(capsys, "retry", "demo/notes-edit:shell")
    assert code == 0 and "next cycle" in out
    loop.cycle()
    assert state()["status"] == "running"


def test_the_loop_drops_a_retry_of_an_abandoned_run(feature):
    feature()
    loop.cycle()
    since = _closed_days_ago(15)
    inbox.post("demo", "notes-edit", "retry", job="shell")
    loop.cycle()
    st = state()
    assert st["status"] == "blocked" and st["closing"] == "done"
    assert st["jobs"]["shell"]["status"] == "blocked"
    assert any(e.endswith(f"a retry was dropped: the run was abandoned on {since}") for e in st["events"])
    assert not inbox.pending("demo", "notes-edit")
