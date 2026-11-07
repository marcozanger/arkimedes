import json
import math

import pytest

from ark import caps, paths


def ledger():
    return paths.runs_dir() / "spend.jsonl"


def test_spending_adds_up_by_day(ark_home):
    assert caps.spent_today() == 0.0
    caps.record("r/s", "a.worker", 1.5)
    caps.record("r/s", "a.judge", 0.25)
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
    assert caps.day_capped({"usd_per_day": 1})


def test_day_limit():
    assert caps.day_limit({}) is None
    assert caps.day_limit({"usd_per_day": None}) is None
    assert caps.day_limit({"usd_per_day": 40}) == 40
    for bad in (0, -1, True, "40", float("nan"), float("inf")):
        with pytest.raises(ValueError):
            caps.day_limit({"usd_per_day": bad})


def test_day_capped(ark_home):
    assert not caps.day_capped({})
    assert not caps.day_capped({"usd_per_day": 1})
    caps.record("r/s", "a.worker", 1)
    assert caps.day_capped({"usd_per_day": 1})
    assert not caps.day_capped({"usd_per_day": 2})


def test_a_run_spends_its_jobs_and_its_other_processes():
    st = {"jobs": {"a": {"usd": 1.5}, "b": {"usd": 2}}, "other_usd": 0.5}
    assert caps.run_spent(st) == 4
    assert caps.run_spent({"jobs": {}}) == 0
    assert caps.run_budget({"a": {"budget": {"usd": 2}}, "b": {"budget": {"usd": 3}}}) == 5


import os
from pathlib import Path

from control_helpers import SLUG, ark, drive, job_status, settled, state

from ark import inbox, loop


def workspace(ark_home, tmp_path, **limits):
    """Limits as given, and a notifier that writes to a file. Returns that file."""
    sent = tmp_path / "sent"
    f = ark_home / "workspace.json"
    ws = json.loads(f.read_text())      # its roots are what makes the demo repo valid
    ws["limits"] = {**ws.get("limits", {}), **limits}
    ws["notify"] = ["sh", "-c", f'printf "%s\\n" "$1" >> "{sent}"', "notify"]
    f.write_text(json.dumps(ws))
    return sent


def said(sent):
    return sent.read_text() if sent.exists() else ""


def test_a_run_over_its_budgets_asks_and_starts_nothing(feature, ark_home, tmp_path, monkeypatch):
    sent = workspace(ark_home, tmp_path)
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
    drive(lambda: (state() or {}).get("cap_hold"))
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
    drive(lambda: "daily spending cap" in said(sent))
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
