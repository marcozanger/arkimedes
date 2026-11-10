import pytest

from ark import loop
from test_loop import bare, receipt, consume_one


def marked(tmp_path, status, kind, **over):
    return bare(tmp_path, status=status, slept_through={kind: 1.0}, **over)


def test_a_worker_that_slept_and_failed_is_not_charged_and_resumes(tmp_path, monkeypatch):
    c = marked(tmp_path, "working", "worker", attempts=2, tier_attempts=1, setup_done=False)
    consume_one(monkeypatch, c, "shell.worker", receipt(exit=1, session="s-1"))
    j = c.st["jobs"]["shell"]
    assert (j["status"], j["attempts"], j["tier_attempts"], j["tier"]) == ("needs_worker", 1, 1, "T1")
    assert j["session"] == "s-1" and "machine slept" in j["resume_msg"]
    assert j["setup_done"] is False and "slept_through" not in j
    assert "not_before" not in j and "infra_tries" not in j


def test_without_a_session_the_worker_starts_fresh(tmp_path, monkeypatch):
    c = marked(tmp_path, "working", "worker", attempts=1)
    consume_one(monkeypatch, c, "shell.worker", receipt(reason="timeout: over its time limit", exit=-15))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and j["session"] is None and j["resume_msg"] is None


def test_the_second_failure_is_charged(tmp_path, monkeypatch):
    c = marked(tmp_path, "working", "worker", attempts=1)
    consume_one(monkeypatch, c, "shell.worker", receipt(exit=1))
    j = c.st["jobs"]["shell"]
    j.update(status="working", attempts=2)
    consume_one(monkeypatch, c, "shell.worker", receipt(exit=1))
    assert j["status"] == "needs_judge" and j["tier_attempts"] == 1


@pytest.mark.parametrize("status,kind,r", [
    ("gating", "gate", receipt(exit=1)),
    ("reviewing", "review", receipt("no json")),
    ("integrating", "integrate", receipt(reason="timeout: over its time limit", exit=-15)),
    ("judging", "judge", receipt("garbage")),
])
def test_other_kinds_go_back_to_waiting_with_no_counter(tmp_path, monkeypatch, status, kind, r):
    c = marked(tmp_path, status, kind, failure="red", tier_path=["T1"])
    consume_one(monkeypatch, c, f"shell.{kind}", r)
    j = c.st["jobs"]["shell"]
    assert j["status"] == loop.WAITING[kind]
    assert j["review_errors"] == 0 and j.get("integrate_lost", 0) == 0 and j["tier_attempts"] == 0
    assert "note" not in j or j["note"] is None


def test_a_clean_success_just_drops_the_mark(tmp_path, monkeypatch):
    c = marked(tmp_path, "gating", "gate")
    consume_one(monkeypatch, c, "shell.gate", receipt())
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_pr" and "slept_through" not in j


def test_a_stop_of_ours_is_never_free(tmp_path, monkeypatch):
    c = marked(tmp_path, "working", "worker", attempts=1, steering="do x")
    consume_one(monkeypatch, c, "shell.worker", receipt(reason="steered", exit=-15))
    assert c.st["jobs"]["shell"]["note"] == "do x"


def test_an_intervene_tick_that_slept_hands_the_worker_back_to_watch(tmp_path, monkeypatch):
    c = bare(tmp_path, status="working", attempts=1, intervening=True, anomaly="over its time for tier T1",
             slept_through={"intervene": 1.0}, soft_deadline=0, watch_from=0, token_factor=1, waited=False)
    consume_one(monkeypatch, c, "shell.intervene", receipt("garbage", meta={"attempt": 1}))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "working" and j["anomaly"] is None and "intervening" not in j
    assert not j["waited"] and "stop_then" not in j


def test_a_decisions_tick_that_slept_starts_again_for_free(tmp_path, monkeypatch):
    c = bare(tmp_path, status="merged")
    c.st.update(closing="decisions", decisions_ticks=1, slept_through=1.0)
    consume_one(monkeypatch, c, loop.DECISIONS, receipt(exit=1))
    assert c.st["closing"] is None and c.st["decisions_ticks"] == 0 and "slept_through" not in c.st
