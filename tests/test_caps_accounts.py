import json
import math

import pytest
from helpers import set_accounts

from ark import accounts, caps, paths


def ledger():
    return paths.runs_dir() / "spend.jsonl"


def test_an_account_is_its_match_or_personal(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": 30}])
    assert accounts.account_of("globallogic/proposals/v0") == "globallogic"
    assert accounts.account_of("demo") == accounts.PERSONAL == "personal"


def test_usd_per_day_on_a_rule(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": 30}, {"match": "acme/"}])
    assert accounts.rule_of_account("globallogic")["usd_per_day"] == 30
    assert "usd_per_day" not in accounts.rule_of_account("acme")
    assert accounts.rule_of_account("personal") == {}


@pytest.mark.parametrize("bad", [0, -1, True, "30", float("nan"), float("inf"), None])
def test_a_bad_usd_per_day_is_an_account_error(ark_home, bad):
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": bad}])
    with pytest.raises(accounts.AccountError):
        accounts.account_of("globallogic/x")


def test_string_keys_still_must_be_non_empty_strings(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/", "gh_user": ""}])
    with pytest.raises(accounts.AccountError):
        accounts.rule_for("globallogic/x")


def test_every_spend_line_names_its_account(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": 30}])
    caps.record("globallogic/proposals/v0/feat", "a.worker", 2)
    caps.record("demo/feat", "a.worker", 1)
    lines = [json.loads(x) for x in ledger().read_text().splitlines()]
    assert [x["account"] for x in lines] == ["globallogic", "personal"]
    assert caps.spent_today("globallogic") == 2
    assert caps.spent_today("personal") == 1
    assert caps.spent_today() == 3


def test_a_line_without_account_counts_for_its_repo(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/"}])
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    ledger().write_text(json.dumps({"day": caps.today(), "run": "globallogic/v0/feat", "usd": 4}) + "\n"
                        + json.dumps({"day": caps.today(), "run": "demo/feat", "usd": 1}) + "\n")
    assert caps.spent_today("globallogic") == 4
    assert caps.spent_today("personal") == 1


def test_limits_per_account(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": 30}, {"match": "acme/"}])
    assert caps.day_limit({"usd_per_day": 40}) == 40                 # personal, as before
    assert caps.day_limit({"usd_per_day": 40}, "globallogic") == 30
    assert caps.day_limit({"usd_per_day": 40}, "acme") is None
    assert caps.day_limit({}, "personal") is None


def test_raises_add_to_today_only(ark_home):
    caps.record_raise("globallogic", 20)
    caps.record_raise("globallogic", 5)
    assert caps.raised_today("globallogic") == 25
    assert caps.raised_today("personal") == 0
    with (paths.runs_dir() / "raises.jsonl").open("a") as f:
        f.write(json.dumps({"day": "2000-01-01", "account": "globallogic", "usd": 99}) + "\n")
    assert caps.raised_today("globallogic") == 25


def test_capped_counts_raises(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": 3}])
    limits = {"usd_per_day": 1000}
    caps.record("globallogic/v0/feat", "a.worker", 3)
    assert caps.capped("globallogic", limits)
    assert not caps.capped("personal", limits)
    caps.record_raise("globallogic", 2)
    assert not caps.capped("globallogic", limits)
    caps.record("globallogic/v0/feat", "a.worker", 2)
    assert caps.capped("globallogic", limits)


def test_no_cap_is_never_capped(ark_home):
    caps.record("demo/feat", "a.worker", 10_000)
    assert not caps.capped("personal", {})


def test_an_unreadable_ledger_caps_every_account(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": 3}])
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    ledger().mkdir()
    assert math.isinf(caps.spent_today("globallogic"))
    assert caps.capped("globallogic", {}) and caps.capped("personal", {"usd_per_day": 1})


def test_check_limits_raises_on_any_bad_cap(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/", "usd_per_day": "x"}])
    with pytest.raises(Exception):
        caps.check_limits({"usd_per_day": 40})
