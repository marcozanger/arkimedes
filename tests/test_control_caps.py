import json
import math

import pytest

from ark import caps, paths


def ledger():
    return paths.runs_dir() / "spend.jsonl"


def test_spending_adds_up_by_day(ark_home):
    assert caps.spent_today() == 0.0
    caps.record("r/s", "a.worker", 1.5)
    caps.record("r/s", "a.euklides", 0.25)
    caps.record("r/s", "a.gate", 0)
    assert caps.spent_today() == 1.75
    assert len(ledger().read_text().splitlines()) == 2
    with ledger().open("a") as f:
        f.write(json.dumps({"day": "2000-01-01", "usd": 99}) + "\n")
    assert caps.spent_today() == 1.75


def test_bad_lines_are_skipped(ark_home):
    caps.record("r/s", "a.worker", 1)
    with ledger().open("a") as f:
        f.write("not json\n[1]\n" + json.dumps({"day": caps.today(), "usd": "x"}) + "\n")
        f.write(json.dumps({"day": caps.today(), "usd": float("nan")}) + "\n")
    assert caps.spent_today() == 1


def test_a_ledger_that_cannot_be_read_counts_as_over(ark_home):
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    ledger().mkdir()
    assert math.isinf(caps.spent_today())
    assert caps.capped("personal", {"usd_per_day": 1})


def test_day_limit():
    assert caps.day_limit({}) is None
    assert caps.day_limit({"usd_per_day": None}) is None
    assert caps.day_limit({"usd_per_day": 40}) == 40
    for bad in (0, -1, True, "40", float("nan"), float("inf")):
        with pytest.raises(ValueError):
            caps.day_limit({"usd_per_day": bad})


def test_a_run_spends_its_jobs_and_its_other_processes():
    st = {"jobs": {"a": {"usd": 1.5}, "b": {"usd": 2}}, "other_usd": 0.5}
    assert caps.run_spent(st) == 4
    assert caps.run_spent({"jobs": {}}) == 0
    assert caps.run_budget({"a": {"budget": {"usd": 2}}, "b": {"budget": {"usd": 3}}}) == 5


import os
from pathlib import Path

from control_helpers import SLUG, ark, drive, job_status, said, settled, state, workspace

from ark import inbox, loop


def test_a_run_over_its_budgets_asks_and_starts_nothing(feature, ark_home, tmp_path, monkeypatch):
    sent = workspace(ark_home, tmp_path)
    margin(ark_home, 0)  # no margin left: the question opens directly, as before Euklides could raise
    monkeypatch.setenv("FAKE_USD", "5")   # the two jobs have $2 each
    feature()
    drive(lambda: (state() or {}).get("cap_hold"))
    st = state()
    q = st["questions"][0]
    assert q["kind"] == "cap_run" and "$5.00" in q["text"] and "$4.00" in q["text"]
    assert "over the $4.00" in said(sent)
    held = st["jobs"]["shell"]["status"]
    for _ in range(3):
        loop.cycle()
    assert job_status("shell") == held and job_status("ui") == "pending"

    inbox.post("demo", SLUG, "answer", id=q["id"], text="raise 100")
    drive(settled)
    st = state()
    assert st["usd_raised"] == 100 and "cap_hold" not in st
    assert {j["status"] for j in st["jobs"].values()} == {"merged"}


def test_stop_ends_a_run_over_its_budgets(feature, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_USD", "5")
    feature()
    drive(lambda: (state() or {}).get("questions"))  # Euklides decides first: the fake asks Marco
    assert "Euklides: needs Marco" in state()["questions"][0]["text"]
    assert state()["questions"][0]["kind"] == "cap_run"
    capsys.readouterr()
    assert ark(capsys, "answer", "--run", f"demo/{SLUG}", "q1", "more please")[0] == 1
    assert ark(capsys, "answer", "--run", f"demo/{SLUG}", "q1", "stop")[0] == 0
    drive(lambda: state()["status"] == "blocked")
    assert state()["reason"] == loop.ENDED and state()["final_pr"] is None


def test_a_run_inside_its_budgets_never_asks(feature):
    feature()
    drive(settled)
    assert "questions" not in state() and "cap_hold" not in state()


def test_the_decisions_step_counts_as_spending(feature, monkeypatch):
    feature()
    drive(lambda: (state() or {}).get("status") == "done")
    assert state()["other_usd"] == 0.01
    assert (paths.runs_dir() / "spend.jsonl").read_text().count('"usd": 0.01') > 3


def test_the_daily_cap_starts_nothing_new_until_it_is_raised(feature, ark_home, tmp_path, monkeypatch):
    sent = workspace(ark_home, tmp_path, usd_per_day=1)
    monkeypatch.setenv("FAKE_USD", "1")
    feature()
    drive(lambda: "personal: daily spending cap" in said(sent))
    for _ in range(20):
        loop.cycle()
    assert job_status("ui") == "pending"
    assert not (Path(os.environ["FAKE_STATE"]) / "ui.worker.1.prompt").exists()
    assert "nothing left to advance" in said(sent)

    workspace(ark_home, tmp_path, usd_per_day=1000)
    drive(settled)
    assert job_status("ui") == "merged"


def test_a_bad_daily_limit_stops_the_cycle_before_it_loses_anything(ark_home, tmp_path):
    workspace(ark_home, tmp_path, usd_per_day="forty")
    with pytest.raises(ValueError):
        loop.cycle()


# ---- a run over its total asks Euklides first -----------------------------------------------------

import json  # noqa: E402

from test_loop import bare, receipt  # noqa: E402

from ark import euklides, questions  # noqa: E402


def margin(ark_home, value):
    f = ark_home / "workspace.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "euklides": {"budget_margin": value}}))


def over_its_total(tmp_path, monkeypatch, **st):
    """One job planned at $2 that spent $2.50; what proc.start was given lands in the returned list."""
    c = bare(tmp_path, status="working", usd=2.5)
    c.st.update(st)
    started = []
    monkeypatch.setattr(loop.proc, "start", lambda d, name, cmd, **kw: started.append((d, name, cmd, kw)))
    return c, started


def test_a_run_over_its_total_with_margin_left_asks_euklides_first(tmp_path, ark_home, monkeypatch):
    margin(ark_home, 0.5)
    c, started = over_its_total(tmp_path, monkeypatch, usd_raised=0.25)
    c.st["jobs"]["shell"]["usd"] = 2.5
    loop._check_run_cap(c)
    [(d, name, cmd, kw)] = started
    assert d == tmp_path and name == "_run.euklides" and kw["cwd"] == loop.ticks_dir()
    assert c.st["cap_hold"] and not c.st.get("questions")
    prompt = cmd[cmd.index("-p") + 1]
    for text in ("spent $2.50", "planned total $2.00", "Marco raised $0.25", "you raised $0.00",
                 "margin left $1.00", "`raise`", "`ask`"):
        assert text in prompt
    assert "`retry`" not in prompt
    tools = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert "Bash" not in tools and "Edit" not in tools and "Write" not in tools and "Read" in tools
    loop._check_run_cap(c)
    assert len(started) == 1  # one call at a time


def test_a_run_level_raise_within_the_margin_lets_the_run_go_on(tmp_path, ark_home, monkeypatch):
    margin(ark_home, 0.5)
    c, _ = over_its_total(tmp_path, monkeypatch)
    loop._check_run_cap(c)
    loop._on_receipt(c, "_run.euklides", receipt(json.dumps({"action": "raise", "usd": 0.75, "note": "almost there"})))
    assert c.st["euklides_raised"] == 0.75 and "cap_hold" not in c.st and not c.st.get("questions")
    assert c.st.get("usd_raised", 0) == 0
    loop._check_run_cap(c)
    assert "cap_hold" not in c.st
    [row] = euklides.decisions()
    assert row["job"] == "_run" and row["action"] == "raise" and row["raise_usd"] == 0.75


@pytest.mark.parametrize("answer", [
    {"action": "raise", "usd": 1.5, "note": "it needs a lot more"},             # past the $1 left
    {"action": "ask", "note": "the run is far over", "proposal": "raise 3"},
])
def test_a_run_level_raise_past_the_margin_or_an_ask_opens_the_cap_question(tmp_path, ark_home, monkeypatch, answer):
    margin(ark_home, 0.5)
    c, _ = over_its_total(tmp_path, monkeypatch)
    loop._check_run_cap(c)
    loop._on_receipt(c, "_run.euklides", receipt(json.dumps(answer)))
    [q] = c.st["questions"]
    assert q["kind"] == "cap_run" and answer["note"] in q["text"] and "$2.50" in q["text"]
    assert ("raise 3" if answer["action"] == "ask" else "raise 1.5") in q["text"]
    assert c.st.get("euklides_raised", 0) == 0 and c.st["cap_hold"]


def test_with_no_margin_left_the_question_opens_directly(tmp_path, ark_home, monkeypatch):
    margin(ark_home, 0.5)
    c, started = over_its_total(tmp_path, monkeypatch, euklides_raised=1.0)
    c.st["jobs"]["shell"]["usd"] = 3.5
    loop._check_run_cap(c)
    assert started == [] and c.st["questions"][0]["kind"] == "cap_run"


def test_marcos_raise_adds_to_usd_raised_not_to_euklides(tmp_path, ark_home, monkeypatch):
    margin(ark_home, 0)
    c, started = over_its_total(tmp_path, monkeypatch, euklides_raised=0.5)
    c.st["jobs"]["shell"]["usd"] = 3
    loop._check_run_cap(c)
    [q] = c.st["questions"]
    assert started == [] and "$2.50" in q["text"]  # planned $2 + Euklides' $0.50
    questions.resolve(c.st, q["id"], "raise 1")
    loop._answered_cap_run(c.st, q, "raise 1")
    assert c.st["usd_raised"] == 1 and c.st["euklides_raised"] == 0.5
    loop._check_run_cap(c)
    assert "cap_hold" not in c.st


def test_a_lost_run_level_call_is_started_again(tmp_path, ark_home, monkeypatch):
    margin(ark_home, 0.5)
    c, started = over_its_total(tmp_path, monkeypatch)
    loop._check_run_cap(c)
    c.st["status"] = "running"
    monkeypatch.setattr(loop.paths, "run_dir", lambda repo, slug: tmp_path)
    loop.reconcile(c.st)
    assert "cap_hold" not in c.st and "cap_euklides" not in c.st
    loop._check_run_cap(c)
    assert len(started) == 2
