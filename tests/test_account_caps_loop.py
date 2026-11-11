import pytest
from control_helpers import SLUG, drive, job_status, said, settled, workspace
from helpers import set_accounts

from ark import accounts, caps, daycap, loop


def test_a_capped_account_starts_nothing_and_says_which(feature, ark_home, tmp_path, monkeypatch):
    sent = workspace(ark_home, tmp_path, usd_per_day=1000)
    set_accounts(ark_home, [{"match": "demo", "usd_per_day": 1}])
    monkeypatch.setenv("FAKE_USD", "1")
    feature()
    drive(lambda: "demo: daily spending cap" in said(sent))
    for _ in range(20):
        loop.cycle()
    assert job_status("ui") == "pending"
    assert "nothing left to advance" in said(sent)
    assert said(sent).count("demo: daily spending cap") == 1
    assert "personal: daily spending cap" not in said(sent)


def test_another_account_at_its_cap_does_not_hold_this_one(feature, ark_home, tmp_path, monkeypatch):
    workspace(ark_home, tmp_path, usd_per_day=1000)
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": 1}])
    caps.record("globallogic/v0/feat", "a.worker", 5)
    assert caps.capped("globallogic", {})
    feature()
    drive(settled)
    assert job_status("ui") == "merged"


def test_a_bad_cap_on_a_rule_stops_the_cycle_before_it_loses_anything(ark_home, tmp_path):
    workspace(ark_home, tmp_path)
    set_accounts(ark_home, [{"match": "demo", "usd_per_day": "x"}])
    with pytest.raises(accounts.AccountError):
        loop.cycle()


def test_a_capped_account_is_only_its_own(ark_home):
    set_accounts(ark_home, [{"match": "work/", "usd_per_day": 1}])
    caps.record("work/r/s", "a.worker", 2)
    st = lambda repo: {"repo": repo, "jobs": {}}
    slots = loop.Slots([st("work/r"), st("demo")], {"usd_per_day": 100})
    assert slots.capped_accounts == {"work"}
    assert slots.capped("work/r") and not slots.capped("demo")


def test_a_capped_account_asks_once_and_a_raise_frees_it(feature, ark_home, tmp_path, monkeypatch):
    sent = workspace(ark_home, tmp_path, usd_per_day=1000)
    set_accounts(ark_home, [{"match": "demo", "usd_per_day": 1}])
    monkeypatch.setenv("FAKE_USD", "1")
    feature()
    drive(lambda: daycap.load()["questions"])
    q = daycap.load()["questions"][0]
    assert q["account"] == "demo" and "$1.00" in q["text"] and f"demo/{SLUG}" in q["text"]
    assert said(sent).count("question c1") == 1
    for _ in range(5):
        loop.cycle()
    assert said(sent).count("question c1") == 1 and job_status("ui") == "pending"
    daycap.post(q["id"], "raise 100")
    drive(settled)
    assert job_status("ui") == "merged"
    assert caps.raised_today("demo") == 100


def test_stop_waits_and_never_asks_again_today(feature, ark_home, tmp_path, monkeypatch):
    workspace(ark_home, tmp_path, usd_per_day=1000)
    set_accounts(ark_home, [{"match": "demo", "usd_per_day": 1}])
    monkeypatch.setenv("FAKE_USD", "1")
    feature()
    drive(lambda: daycap.load()["questions"])
    daycap.post("c1", "stop")
    for _ in range(10):
        loop.cycle()
    d = daycap.load()
    assert d["questions"] == [] and d["stopped"] == ["demo"] and job_status("ui") == "pending"


def test_raising_the_config_closes_the_question(feature, ark_home, tmp_path, monkeypatch):
    workspace(ark_home, tmp_path, usd_per_day=1000)
    set_accounts(ark_home, [{"match": "demo", "usd_per_day": 1}])
    monkeypatch.setenv("FAKE_USD", "1")
    feature()
    drive(lambda: daycap.load()["questions"])
    set_accounts(ark_home, [{"match": "demo", "usd_per_day": 1000}])
    drive(settled)
    assert daycap.load()["questions"] == []
