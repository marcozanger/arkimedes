from control_helpers import ark, change_state, drive, settled, state

from ark import forge, history, inbox, loop, paths, run


def _close_blocked(final_pr=None):
    st = state()
    st.update(status="blocked", closing="done", final_pr=final_pr)
    run.save(st)


def test_a_run_finished_before_the_times_were_kept_gets_none(monkeypatch):
    monkeypatch.setattr(run, "now", lambda: "T")
    st = {"status": "blocked", "closing": "done", "jobs": {"a": {"status": "merged"}, "b": {"status": "blocked"}}}
    run.stamp_times(st)
    assert st["jobs"]["a"] == {"status": "merged"} and st["jobs"]["b"] == {"status": "blocked"}
    assert st["times_kept"]
    st["status"], st["closing"] = "running", None   # reopened: it is a live run now
    run.stamp_times(st)
    assert st["jobs"]["a"]["started"] == "T"


def test_a_live_run_without_the_mark_is_stamped(monkeypatch):
    monkeypatch.setattr(run, "now", lambda: "T")
    st = {"status": "running", "closing": None, "jobs": {"a": {"status": "working"}}}
    run.stamp_times(st)
    assert st["jobs"]["a"]["started"] == "T"


def test_abort_ends_a_blocked_run_for_good(feature, capsys):
    feature()
    loop.cycle()
    change_state(shell={"status": "blocked", "reason": "x", "blocked_from": "working"})
    _close_blocked()
    code, out = ark(capsys, "abort", "demo/notes-edit", "--why", "never done")
    assert code == 0 and "aborted" in out
    loop.cycle()
    st = state()
    assert st["status"] == "aborted" and st["closing"] == "done" and st["reason"] == "never done"
    assert not inbox.pending("demo", "notes-edit")
    ark(capsys, "retry", "demo/notes-edit:shell")   # the job is blocked, so the post is accepted ...
    loop.cycle()
    assert state()["status"] == "aborted"            # ... and does nothing
    rows = history._read("plans.jsonl")
    assert [r["status"] for r in rows if r["plan"] == "notes-edit"] == ["aborted"]


def test_abort_refuses_a_run_that_is_running(feature, capsys):
    feature()
    loop.cycle()
    code, out = ark(capsys, "abort", "demo/notes-edit")
    assert code == 1 and "stop its jobs first" in out
    assert not inbox.pending("demo", "notes-edit")


def test_abort_refuses_an_unknown_plan(capsys):
    code, out = ark(capsys, "abort", "demo/nothing")
    assert code == 1 and "has no run" in out


def test_an_aborted_run_is_not_live(feature):
    feature()
    loop.cycle()
    st = state()
    st.update(status="aborted", closing="done")
    run.save(st)
    loop.cycle()
    assert state()["status"] == "aborted"
    assert "aborted" in run.FINISHED


def test_a_blocked_run_whose_final_pr_is_merged_is_done(feature):
    feature()
    drive(settled)
    drive(lambda: state()["status"] in run.FINISHED)
    st = state()
    pr = st["final_pr"]
    assert pr, "the run opened a final PR"
    change_state(**{})
    st = state()
    st.update(status="blocked", closing="done")
    run.save(st)
    loop._final_pr_seen.clear()
    loop.cycle()
    assert state()["status"] == "blocked"                    # the PR is still open
    repo = paths.find_repo("demo")
    prs = forge.LocalForge().list(repo)
    prs[pr["number"] - 1]["state"] = "MERGED"
    forge.LocalForge()._save(repo, prs)
    loop._final_pr_seen.clear()
    loop.cycle()
    assert state()["status"] == "done"
    assert "is merged: the run is done" in state()["events"][-1]
