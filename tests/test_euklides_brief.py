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


def test_blast_radius_gives_the_reserved_list_and_asks_for_the_reach(ark_home):
    p = euk(allowed=["retry", "widen", "fix_base", "ask"])
    assert "## What is yours" in p and "Everything is your call except this reserved list" in p
    for r in ("R1 ", "R2 ", "R3 ", "R4 ", "R5 ", "R6 "):
        assert r in p
    assert "1 to 20 exact paths" in p and "small-fix rule" not in p and "through a reviewer" in p
    assert "`plan` and `contract` make your decision a question to Marco" in p
    assert p.index("## What is yours") < p.index("## Decide")


def test_legacy_keeps_the_old_actions_text(ark_home):
    import json
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "euklides": {"mode": "legacy"}}))
    p = euk(allowed=["retry", "widen", "fix_base", "ask"])
    assert "## What is yours" not in p and "1 to 3 exact paths" in p and "small-fix rule" in p


def test_a_job_level_ask_shows_label_and_alternative_in_the_reply_format():
    p = euk(allowed=["unblock", "ask"])
    [line] = [x for x in p.splitlines() if '"action": "ask"' in x]
    assert '"label"' in line and '"alternative"' in line
    flat = " ".join(p.split())
    assert "at most 60 characters" in flat and "Never offer `stop`" in flat and "different `then`" in flat
    assert "label" not in euk(allowed=["unblock"]).split("## Decide")[1]


def test_a_run_level_brief_has_no_label_or_alternative():
    st, _, _ = setup(Path(tempfile.mkdtemp()))
    p = brief.euklides_run_prompt(st, spent=12, planned=10, usd_raised=0, euklides_raised=0, margin_left=5,
                                  allowed=["raise", "ask"], lessons="")
    assert "label" not in p and "alternative" not in p


def test_the_role_file_describes_label_and_alternative():
    role = (Path(__file__).resolve().parents[1] / "agents" / "euklides.md").read_text()
    flat = " ".join(role.split())
    assert "`label`" in flat and "`alternative`" in flat and "Never offer `stop`" in flat
    [schema] = [x for x in role.splitlines() if x.startswith('{"action"')]
    assert '"label"' in schema and '"alternative"' in schema
