import tempfile
from pathlib import Path

from test_brief import setup

from ark import brief


def euk(**over):
    st, plan, mj = setup(Path(tempfile.mkdtemp()))
    st["answers"] = [{"id": "q1", "job": "shell", "question": "May I fix the docs?", "answer": "yes"},
                     {"id": "q2", "job": "ui", "question": "other job", "answer": "no"}]
    st["events"] = [f"t · event {i}" for i in range(30)]
    j = {"tier": "T1", "tier_path": ["T1"], "attempts": 1, "usd": 0.5}
    kw = dict(trigger="blocked", situation="integration gate red", allowed=["unblock", "ask"], report="",
              dependents=[], worktree="/wt", log="/log", lessons="",
              earlier=[{"id": "demo/notes-edit/shell/1", "action": "retry", "note": "fix the import"}],
              base_check="red on ark/notes-edit without the job too")
    kw.update(over)
    return brief.euklides_prompt(st, "shell", j, mj["shell"], plan, **kw)


def test_the_brief_holds_the_jobs_earlier_answers_and_decisions():
    p = euk()
    assert "May I fix the docs?" in p and "yes" in p and "other job" not in p
    assert "fix the import" in p


def test_the_brief_holds_the_last_events_only():
    p = euk()
    assert "event 29" in p and "event 15" in p and "event 14" not in p


def test_the_brief_explains_how_a_run_works():
    p = euk()
    assert "never synced with main" in p and "grants" in p


def test_the_brief_says_what_the_base_check_found():
    assert "Base check: red on ark/notes-edit without the job too" in euk()
    assert "Base check: not run" in euk(base_check=None)


def test_an_open_ask_lists_every_then_with_one_line_each():
    p = euk(allowed=["unblock", "ask"])
    for then in ("go_on", "escalate", "widen", "fix_base", "raise", "accept", "stop"):
        assert sum(1 for line in p.splitlines() if line.startswith(f"  - `{then}`")) == 1, then
    [line] = [x for x in p.splitlines() if '"action": "ask"' in x]
    assert '"then"' in line


def test_a_way_out_the_runner_cannot_carry_out_goes_in_the_note():
    p = " ".join(euk(allowed=["ask"]).split())
    assert ("a way out the runner cannot carry out (a PR to main, a plan edit, a push by hand) goes in `note` as "
            "what Marco must do himself; never as `then`") in p


def test_then_shows_only_when_ask_is_open():
    assert "`fix_base`: fix the integration branch" not in euk(allowed=["unblock"])


def test_the_accept_line_says_when_to_use_it_and_when_never():
    p = " ".join(euk(allowed=["ask"]).split())
    assert "already committed" in p and "narrower form of the job's own gate" in p and "never to skip a red test" in p
