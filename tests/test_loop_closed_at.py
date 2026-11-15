"""Every way a run closes stamps closed_at; a retry that reopens it clears the stamp."""
from datetime import datetime, timedelta, timezone

from control_helpers import ark, change_state, drive, job_status

from ark import gitops, loop, run
from test_loop import SLUG, feature, state  # noqa: F401


def closed():
    st = state()
    return bool(st) and st.get("closing") == "done"


def stamp(st):
    t = datetime.fromisoformat(st["closed_at"])
    assert t.tzinfo is not None and t.utcoffset().total_seconds() == 0
    return t


def test_a_run_that_ends_done_has_closed_at(feature):
    feature()
    drive(closed)
    st = state()
    assert st["status"] == "done"
    stamp(st)


def test_a_run_stopped_in_closing_has_closed_at(feature, monkeypatch):
    feature()
    drive(lambda: all(j["status"] == "merged" for j in (state() or {}).get("jobs", {"x": {"status": "-"}}).values()))

    def refused(*a, **k):
        raise gitops.PathRefused("the branch will not take this path")
    monkeypatch.setattr(gitops, "commit_file", refused)
    drive(closed)
    st = state()
    assert st["status"] == "blocked"
    stamp(st)


def test_a_run_ended_by_marco_has_closed_at(feature):
    feature()
    loop.cycle()
    st = state()
    loop._begin_end(st, loop.ENDED)
    run.save(st)
    drive(closed)
    st = state()
    assert st["status"] == "blocked" and st["reason"] == loop.ENDED
    stamp(st)


def test_an_aborted_run_has_closed_at(feature, capsys):
    feature()
    loop.cycle()
    change_state(shell={"status": "blocked", "reason": "x", "blocked_from": "working"})
    st = state()
    st.update(status="blocked", closing="done")
    run.save(st)
    assert ark(capsys, "abort", f"demo/{SLUG}", "--why", "never")[0] == 0
    loop.cycle()
    st = state()
    assert st["status"] == "aborted"
    stamp(st)


def test_a_reopened_run_loses_closed_at_and_gets_a_new_one_when_it_closes_again(feature, capsys):
    feature()
    loop.cycle()
    change_state(shell={"status": "blocked", "reason": "x", "blocked_from": "working"},
                 ui={"status": "blocked", "reason": "a job it depends on is blocked: shell", "waiting_on": "shell"})
    st = state()
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")  # inside the window
    st.update(status="blocked", closing="done", closed_at=old)
    run.save(st)
    assert ark(capsys, "retry", f"demo/{SLUG}:shell")[0] == 0
    loop.cycle()
    st = state()
    assert st["status"] == "running" and "closed_at" not in st
    drive(closed)
    st = state()
    assert job_status("shell") == "merged"
    assert stamp(st) > datetime.fromisoformat(old)
    assert stamp(st) <= datetime.now(timezone.utc)
