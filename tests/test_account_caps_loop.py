import pytest
from control_helpers import drive, job_status, said, settled, workspace
from helpers import set_accounts

from ark import accounts, caps, loop


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
