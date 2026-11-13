import time

import pytest
from test_loop import SLUG, bare, consume_one, down, drive, receipt, state, stopping  # noqa: F401
from helpers import job, manifest

from ark import gitops, inbox, loop, proc, questions, run


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(loop.time, "time", lambda: now[0])
    return now


def test_a_passing_fault_waits_1_2_4_8_15_minutes_then_goes_to_euklides(tmp_path, clock):
    c = bare(tmp_path, status="needs_push")
    waits = []
    for _ in range(5):
        loop._passing(c, "shell", "push_tries", "push refused", "needs_push")
        j = c.st["jobs"]["shell"]
        assert j["status"] == "needs_push"
        waits.append(j["not_before"] - clock[0])
    assert waits == [60, 120, 240, 480, 900]
    loop._passing(c, "shell", "push_tries", "push refused", "needs_push")
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides"
    assert j["euklides"]["trigger"] == "blocked" and j["euklides"]["back"] == "needs_push"
    assert "push refused" in j["euklides"]["situation"] and "5 retries" in j["euklides"]["situation"]
    assert "push_tries" not in j and "not_before" not in j
    assert c.st["alerts"] == []  # Marco hears nothing while it heals


def test_euklides_going_on_after_a_passing_fault_returns_to_that_step(tmp_path):
    c = bare(tmp_path, status="needs_euklides", session="s1",
             euklides={"trigger": "blocked", "situation": "push refused", "kind": "step", "back": "needs_push"})
    loop._go_on(c.st, "shell", c.st["jobs"]["shell"].pop("euklides"), "push again")
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_push" and not j["resume_msg"]


def test_an_error_on_a_waiting_step_is_a_passing_fault_even_from_git(tmp_path, clock):
    c = bare(tmp_path, status="needs_push", head="a" * 40)
    for _ in range(6):
        loop._error(c, "shell", gitops.GitError("network is unreachable"))
        clock[0] += 1000
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and "network is unreachable" in j["euklides"]["situation"]
    assert j["euklides"]["back"] == "needs_push"


def test_a_step_that_goes_through_clears_its_tries(tmp_path, monkeypatch):
    c = bare(tmp_path, status="needs_push", step_tries=3, head="a" * 40)
    monkeypatch.setattr(loop, "_next_step", lambda c, job, j, slots: lambda: None)
    loop.schedule(c, None)
    assert "step_tries" not in c.st["jobs"]["shell"]


@pytest.mark.parametrize("status,kind,back", [("working", "worker", "needs_worker"),
                                              ("reviewing", "review", "needs_review")])
def test_an_unreachable_model_is_a_passing_fault(tmp_path, monkeypatch, clock, status, kind, back):
    c = bare(tmp_path, status=status, session="s1")
    for _ in range(6):
        loop._model_unreachable(c, "shell", kind)
        clock[0] += 1000
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and j["euklides"]["back"] == back
    assert not any("unreachable" in a for a in c.st.get("critical", []))


def test_an_unreachable_euklides_keeps_backing_off_and_alerts(tmp_path, clock):
    c = bare(tmp_path, status="euklides")
    for _ in range(3):
        loop._model_unreachable(c, "shell", "euklides")
        c.st["jobs"]["shell"]["status"] = "euklides"
    assert c.st["jobs"]["shell"]["status"] == "euklides"
    assert any("unreachable" in a for a in c.st["critical"])


def test_processes_left_in_the_worktree_retry_then_go_to_euklides(tmp_path, monkeypatch, clock):
    c = bare(tmp_path, status="needs_worker")
    monkeypatch.setattr(proc, "cwd_holders", lambda wt: [4242])
    for _ in range(6):
        assert loop._worktree_free(c, "shell") is False
        clock[0] += 1000
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and "4242" in j["euklides"]["situation"]
    assert j["euklides"]["back"] == "needs_worker" and c.st["alerts"] == []


def test_an_integration_check_with_no_result_retries_on_the_schedule(tmp_path, clock):
    c = bare(tmp_path, status="integrating")
    for _ in range(6):
        c.st["jobs"]["shell"]["status"] = "integrating"
        loop.on_integrate(c, "shell", receipt(reason="killed"))
        clock[0] += 1000
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and j["euklides"]["back"] == "needs_integrate"


def test_the_intervene_ticks_stop_block_goes_to_euklides(tmp_path):
    c = bare(tmp_path, status="working")
    loop._after_stop(c.st, "shell", {"action": "stop_block", "note": "it loops on the same file"})
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and "it loops on the same file" in j["euklides"]["situation"]


def test_marcos_stop_still_blocks_at_once_and_remembers_the_step(tmp_path):
    c = bare(tmp_path, status="needs_gate")
    loop._block(c.st, "shell", proc.STOPPED_BY_MARCO)
    j = c.st["jobs"]["shell"]
    assert j["status"] == "blocked" and j["blocked_from"] == "needs_gate"


def test_a_dependent_of_a_blocked_job_waits_and_comes_back(feature):
    feature()
    loop.cycle()
    st = state()
    st["jobs"]["shell"].update(status="blocked", reason="x")
    run.save(st)
    loop.cycle()
    ui = state()["jobs"]["ui"]
    assert ui["status"] == "waiting_dep" and ui["waiting_on"] == "shell"
    st = state()
    st["jobs"]["shell"]["status"] = "merged"
    run.save(st)
    loop.cycle()
    assert state()["jobs"]["ui"]["status"] != "waiting_dep" and "waiting_on" not in state()["jobs"]["ui"]


def test_a_run_whose_only_live_job_waits_on_a_blocked_one_closes_blocked(feature, monkeypatch):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    feature()
    drive(stopping(lambda: state()["status"] == "blocked"))
    jobs = state()["jobs"]
    assert jobs["shell"]["status"] == "blocked"
    assert jobs["ui"]["status"] == "blocked" and "depends on is blocked: shell" in jobs["ui"]["reason"]
    assert jobs["ui"]["waiting_on"] == "shell"


def test_a_run_with_an_open_question_stays_open_and_reminds_daily(feature, monkeypatch, clock):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "ask", "note": "may it?", "proposal": null}')
    feature()
    clock[0] = time.time()
    drive(lambda: state()["jobs"]["shell"]["status"] == "waiting_answer")
    said = []

    def cycle():
        said.extend(loop.cycle()[1])  # a cycle hands its alerts over: the state keeps none

    for _ in range(5):
        cycle()
    st = state()
    assert st["status"] == "running" and st["jobs"]["ui"]["status"] == "waiting_dep"
    before = len([a for a in said if "still waiting for your answer" in a])
    clock[0] += 86400 + 1
    cycle()
    cycle()
    after = [a for a in said if "still waiting for your answer" in a]
    assert len(after) == before + 1


def test_ps_says_what_a_waiting_job_waits_on():
    from ark import ctl
    st = {"repo": "demo", "slug": SLUG, "jobs": {}}
    row = ctl._ps_row(st, "ui", {**run.new_job(job("ui", ["T3"], ["src/ui/**"])), "status": "waiting_dep",
                                 "waiting_on": "shell"})
    assert row[-1] == "waiting on shell"
