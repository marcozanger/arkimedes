"""An `ok` the runner cannot carry out must not resume a worker: `accept` (a replacement gate, no worker), the
effect check after every ok, the over-budget notice, and `ark retry` against live runs of the same repo."""
import json

import pytest
from control_helpers import ark, change_state, state as run_state
from helpers import manifest
from test_loop import (SLUG, at_euklides, asked_then, bare, drive, euk_answer, feature, jobs_settled,  # noqa: F401
                       receipt, state)

from ark import approve, inbox, loop, merge, questions, run

HEAD = "a" * 40


def accepting(tmp_path, monkeypatch, gate="pytest --changed", clean=True, head=HEAD, **over):
    """A job at a question whose ask is `accept`; its worktree is `tmp_path`, committed and clean (or not)."""
    monkeypatch.setattr(loop, "_wt", lambda st, job: tmp_path)
    monkeypatch.setattr(loop.gitops, "is_clean", lambda wt: clean)
    monkeypatch.setattr(loop.gitops, "rev", lambda wt, ref: "f" * 40)
    c = at_euklides(tmp_path, trigger="question", allowed=("ask",), situation="which file?", session="s1",
                    head=head, **over)
    loop.on_euklides(c, "shell", euk_answer(action="ask", note="the e2e failures are old",
                                            proposal="accept the head", then="accept", gate=gate))
    return c


# ---- accept ---------------------------------------------------------------------------------------

def test_the_question_names_the_gate_the_ok_will_run(tmp_path, ark_home, monkeypatch):
    c = accepting(tmp_path, monkeypatch)
    [q] = c.st["questions"]
    assert "Answer ok to: accept shell's head on the gate `pytest --changed`" in q["text"]
    assert c.st["jobs"]["shell"]["euklides_ask"]["gate"] == "pytest --changed"


def test_ok_to_an_accept_queues_the_head_for_the_gate_and_starts_no_worker(tmp_path, ark_home, monkeypatch):
    c = accepting(tmp_path, monkeypatch)
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_push" and j["accept_gate"] == "pytest --changed" and j["head"] == "f" * 40
    assert not j.get("resume_msg") and j["attempts"] == 0 and "euklides_ask" not in j
    assert any("replacement gate" in e and "no worker" in e for e in c.st["events"])
    [a] = c.st["amendments"]
    assert (a["job"], a["kind"], a["after"], a["by"]) == ("shell", "gate", "pytest --changed", "Marco")


@pytest.mark.parametrize("kw,why", [({"head": None}, "no committed head"), ({"clean": False}, "uncommitted changes")])
def test_an_accept_without_a_clean_committed_head_is_not_carried_out(tmp_path, ark_home, monkeypatch, kw, why):
    c = accepting(tmp_path, monkeypatch, **kw)
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and "accept_gate" not in j and not j.get("resume_msg")
    assert any("was not carried out" in a and why in a for a in c.st["alerts"])
    [q] = c.st["questions"]
    assert "plan edit" in q["text"] and "Answer ok to: stop" in q["text"]


def test_a_green_replacement_gate_goes_to_the_pr_and_is_recorded_with_its_gate(tmp_path):
    c = bare(tmp_path, status="gating", accept_gate="pytest --changed")
    loop.on_gate(c, "shell", receipt("ok"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_pr" and "accept_gate" not in j
    rec = merge.read_record(tmp_path, "shell", "gate")
    assert rec["ok"] is True and rec["accepted_gate"] == "pytest --changed"
    assert any("replacement gate" in e for e in c.st["events"])


def test_a_red_replacement_gate_asks_again_with_stop_and_the_tail(tmp_path):
    c = bare(tmp_path, status="gating", accept_gate="pytest --changed", head=HEAD)
    loop.on_gate(c, "shell", receipt("2 failed: test_x", exit=1))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and "accept_gate" not in j and j["tier_attempts"] == 0
    assert j["euklides_ask"]["then"] == "stop"
    [q] = c.st["questions"]
    assert "replacement gate `pytest --changed` is red" in q["text"] and "2 failed: test_x" in q["text"]
    assert merge.read_record(tmp_path, "shell", "gate")["ok"] is False


def test_the_gate_that_starts_is_the_replacement_and_only_once(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(loop.proc, "start", lambda d, name, cmd, **kw: seen.append(cmd))
    monkeypatch.setattr(loop.gitops, "origin_url", lambda repo: "")
    c = bare(tmp_path, status="needs_gate", accept_gate="pytest --changed", head=HEAD)
    loop.start_gate(c, "shell")
    assert seen == [["sh", "-c", "pytest --changed"]]
    assert any("replacement gate" in a for a in c.st["alerts"])
    c.st["jobs"]["shell"].pop("accept_gate")
    loop.start_gate(c, "shell")
    assert seen[1] == ["sh", "-c", "true"]  # the plan's own gate again


def test_a_blocked_job_forgets_a_replacement_gate_on_retry():
    assert "accept_gate" in loop.RETRY_CLEARS


# the mcp-server story: ok to an accept starts no new worker; green integrates, red asks again

def asked_accept(monkeypatch, gate):
    monkeypatch.setenv("FAKE_EUKLIDES", json.dumps({"action": "ask", "note": "the head is fine", "undo": None,
                                                    "proposal": "accept it on the narrow gate", "then": "accept",
                                                    "gate": gate}))
    drive(lambda: (state() or {}).get("jobs") and state()["jobs"]["shell"]["status"] == "waiting_answer")
    assert state()["jobs"]["shell"]["attempts"] == 1


def test_an_ok_to_an_accept_integrates_the_job_with_no_new_worker(feature, monkeypatch):
    feature(manifest(jobs=[loop_job("exit 1")]))
    asked_accept(monkeypatch, "true")
    [q] = state()["questions"]
    inbox.post("demo", SLUG, "answer", id=q["id"], text="ok")
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 1
    assert merge.read_record(run_dir(), "shell", "gate")["accepted_gate"] == "true"


def test_an_ok_to_an_accept_whose_gate_is_red_asks_again_and_starts_no_worker(feature, monkeypatch):
    feature(manifest(jobs=[loop_job("exit 1")]))
    asked_accept(monkeypatch, "exit 1")
    [q] = state()["questions"]
    inbox.post("demo", SLUG, "answer", id=q["id"], text="ok")
    drive(lambda: any(x["id"] != q["id"] for x in state().get("questions") or []))
    j = state()["jobs"]["shell"]
    [q2] = state()["questions"]
    assert j["status"] == "waiting_answer" and j["attempts"] == 1
    assert "replacement gate `exit 1` is red" in q2["text"] and j["euklides_ask"]["then"] == "stop"


def loop_job(gate):
    from helpers import job
    return job("shell", ["T1", "T2", "T3"], ["src/shell/**"], gate=gate)


def run_dir():
    from ark import paths
    return paths.run_dir("demo", SLUG)


# ---- the effect of every ok ----------------------------------------------------------------------

def test_a_widen_that_leaves_no_grant_is_reported_and_asks_again(tmp_path, ark_home, monkeypatch):
    c = asked_then(tmp_path, then="widen", files=["x.py"])
    monkeypatch.setattr(loop, "_add_grant", lambda *a, **k: None)
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and not j.get("resume_msg")
    assert any("Marco's ok to widen was not carried out: x.py did not join" in a for a in c.st["alerts"])
    [q] = c.st["questions"]
    assert q["id"] == "q2" and "plan edit" in q["text"] and "Answer ok to: stop shell" in q["text"]


def test_a_fix_base_that_starts_no_fix_is_reported(tmp_path, ark_home, monkeypatch):
    c = asked_then(tmp_path, trigger="blocked", kind="integrate", note="delete bad.txt", then="fix_base")
    monkeypatch.setattr(loop, "_start_base_fix", lambda *a, **k: None)
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    assert c.st["jobs"]["shell"]["status"] == "waiting_answer"
    assert any("fix_base was not carried out: no base fix" in a for a in c.st["alerts"])


def test_an_ok_that_works_leaves_no_alert(tmp_path, ark_home):
    c = asked_then(tmp_path, then="raise", usd=2)
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    assert c.st["jobs"]["shell"]["status"] == "needs_worker"
    assert not [a for a in c.st["alerts"] if "not carried out" in a]


def test_an_ok_to_stop_is_not_checked_and_blocks(tmp_path, ark_home):
    c = asked_then(tmp_path, note="Marco lands it", then="stop")
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    assert c.st["jobs"]["shell"]["status"] == "blocked"


def test_effect_ok_per_then(tmp_path):
    c = bare(tmp_path)
    st, j = c.st, c.st["jobs"]["shell"]
    before = {"usd_raised": 0, "tier": "T1"}
    assert loop._effect_ok(st, "shell", "raise", {}, before)
    j["usd_raised"] = 1
    assert loop._effect_ok(st, "shell", "raise", {}, before) is None
    assert loop._effect_ok(st, "shell", "escalate", {}, before)  # T1 -> T1 with a tier above: it did not move
    j["tier"] = "T2"
    assert loop._effect_ok(st, "shell", "escalate", {}, before) is None
    assert loop._effect_ok(st, "shell", "escalate", {}, {"usd_raised": 0, "tier": "T3"}) is None  # already the top
    assert loop._effect_ok(st, "shell", "widen", {"files": ["x.py"]}, before)
    st["grants"] = {"shell": ["x.py"]}
    assert loop._effect_ok(st, "shell", "widen", {"files": ["x.py"]}, before) is None
    assert loop._effect_ok(st, "shell", "fix_base", {}, before)
    st["fixes"] = [{"status": "working", "waiting": {"shell": {}}}]
    assert loop._effect_ok(st, "shell", "fix_base", {}, before) is None
    assert loop._effect_ok(st, "shell", "go_on", {}, before) is None


# ---- a worker on an answer, past the budget --------------------------------------------------------

def test_a_worker_resumed_past_its_budget_is_said_once(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "_budget_usd", lambda st, job: 2.0)
    c = bare(tmp_path, status="waiting_answer", usd=2.5)
    e = {"trigger": "question", "situation": "which?", "kind": None}
    loop._go_on(c.st, "shell", e, "use sqlite")
    loop._go_on(c.st, "shell", e, "use sqlite")
    alerts = [a for a in c.st["alerts"] if "over its budget" in a]
    assert len(alerts) == 1 and "$2.50 spent of $2.00" in alerts[0]
    assert c.st["jobs"]["shell"]["status"] == "needs_worker"  # not blocked: Marco may have chosen to go on


def test_a_raise_makes_a_new_notice_possible_and_under_budget_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "_budget_usd", lambda st, job: 2.0)
    c = bare(tmp_path, status="waiting_answer", usd=1.5)
    e = {"trigger": "question", "situation": "which?", "kind": None}
    loop._go_on(c.st, "shell", e, "go")
    assert not c.st["alerts"]
    j = c.st["jobs"]["shell"]
    j.update(usd=2.0, usd_raised=1.0)
    loop._go_on(c.st, "shell", e, "go")
    assert not c.st["alerts"]
    j["usd"] = 3.2
    loop._go_on(c.st, "shell", e, "go")
    assert len(c.st["alerts"]) == 1 and "$3.20 spent of $3.00" in c.st["alerts"][0]


def test_a_step_that_goes_back_is_not_a_worker_and_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "_budget_usd", lambda st, job: 1.0)
    c = bare(tmp_path, usd=5)
    loop._go_on(c.st, "shell", {"trigger": "blocked", "situation": "s", "kind": "step", "back": "needs_push"}, "n")
    assert not c.st["alerts"]


def test_the_budget_comes_from_the_plan(feature):
    feature()
    loop.cycle()
    assert loop._budget_usd(state(), "shell") == 2
    assert loop._budget_usd({"plan": "/nowhere"}, "shell") is None


# ---- ark retry against the live runs of the repo ---------------------------------------------------

def closed_blocked(feature, capsys):
    feature()
    loop.cycle()
    change_state(shell={"status": "blocked", "reason": "x", "blocked_from": "working"})
    st = state()
    st.update(status="blocked", closing="done", decisions_sha="b" * 40)
    run.save(st)


def live_run(area, slug="other", status="running"):
    st = run.new_state({"repo": "demo", "slug": slug, "plan": "/nowhere", "sha256": "0" * 64, "approved_at": "t"})
    st.update(status=status, area=area)
    run.save(st)


def test_area_clash_names_the_other_run_and_the_globs():
    mine = {"repo": "demo", "slug": "a", "area": ["src/shell/**"], "status": "blocked"}
    other = {"repo": "demo", "slug": "b", "area": ["src/shell/x/**"], "status": "running"}
    got = approve.area_clash(mine, [mine, other])
    assert "running plan b" in got and "src/shell/**" in got and "src/shell/x/**" in got
    assert approve.area_clash(mine, [{**other, "area": ["src/ui/**"]}]) is None
    assert approve.area_clash(mine, [{**other, "status": "done"}]) is None
    assert approve.area_clash(mine, [{**other, "repo": "else"}]) is None


def test_retry_of_a_closed_run_is_refused_over_a_live_overlap(feature, capsys):
    closed_blocked(feature, capsys)
    live_run(["src/shell/**"])
    code, out = ark(capsys, "retry", "demo/notes-edit:shell")
    assert code == 1 and "other" in out and "src/shell/**" in out
    assert not inbox.pending("demo", SLUG)


def test_retry_of_a_closed_run_goes_when_nothing_live_overlaps(feature, capsys):
    closed_blocked(feature, capsys)
    live_run(["docs/**"])
    code, out = ark(capsys, "retry", "demo/notes-edit:shell")
    assert code == 0 and inbox.pending("demo", SLUG)


def test_the_loop_refuses_a_retry_that_slipped_in_and_says_why(feature, capsys):
    closed_blocked(feature, capsys)
    inbox.post("demo", SLUG, "retry", job="shell")
    live_run(["src/shell/**"])
    loop._reopen(state(), [state(), run.load("demo", "other")])
    assert not inbox.pending("demo", SLUG)
    assert state()["status"] == "blocked"
    loop.cycle()
    assert state()["status"] == "blocked" and state()["jobs"]["shell"]["status"] == "blocked"


def test_the_loop_reopens_a_closed_run_when_nothing_live_overlaps(feature, capsys):
    closed_blocked(feature, capsys)
    inbox.post("demo", SLUG, "retry", job="shell")
    st = state()
    loop._reopen(st, [st])
    assert st["status"] == "running"


def test_a_refusal_is_told_once_for_each_reason(tmp_path):
    st = bare(tmp_path).st
    a = {"sha256": "s" * 64}
    assert loop._first_refusal(st, a, "it already finished")
    assert not loop._first_refusal(st, a, "it already finished")
    assert loop._first_refusal(st, a, "its area overlaps the running plan x")
    assert not loop._first_refusal(st, a, "its area overlaps the running plan x")
    assert loop._first_refusal(st, {"sha256": "t" * 64}, "its area overlaps the running plan x")


# ---- ok 2: Euklides' alternative, with the same effect check -------------------------------------

def test_ok_2_to_a_raise_alternative_raises_and_is_checked(tmp_path, ark_home):
    c = asked_then(tmp_path, then="escalate", alternative={"label": "Raise $2", "then": "raise", "usd": 2})
    loop._apply_answer(c.st, {"id": "q1", "text": "ok 2"})
    j = c.st["jobs"]["shell"]
    assert j["usd_raised"] == 2 and j["marco_raised"] == 2 and j["tier"] == "T1" and j["status"] == "needs_worker"
    assert not [a for a in c.st["alerts"] if "not carried out" in a]


def test_ok_2_to_a_raise_alternative_that_leaves_no_trace_asks_again(tmp_path, ark_home, monkeypatch):
    c = asked_then(tmp_path, then="escalate", alternative={"label": "Raise $2", "then": "raise", "usd": 2})
    seen = []
    monkeypatch.setattr(loop, "_run_then", lambda st, job, q, ask, before: seen.append(ask))
    loop._apply_answer(c.st, {"id": "q1", "text": "ok 2"})
    assert [(a["then"], a["usd"]) for a in seen] == [("raise", 2)]
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and j.get("usd_raised", 0) == 0
    assert any("Marco's ok to raise was not carried out: the budget did not grow" in a for a in c.st["alerts"])


def test_ok_2_to_an_escalate_alternative_that_leaves_no_trace_asks_again(tmp_path, ark_home, monkeypatch):
    c = asked_then(tmp_path, then="raise", usd=2, alternative={"label": "Move it up", "then": "escalate"})
    monkeypatch.setattr(loop, "_escalate", lambda j: None)
    loop._apply_answer(c.st, {"id": "q1", "text": "ok 2"})
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and j["tier"] == "T1" and j.get("usd_raised", 0) == 0
    assert any("Marco's ok to escalate was not carried out: the tier did not change" in a for a in c.st["alerts"])
