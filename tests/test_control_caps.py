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
