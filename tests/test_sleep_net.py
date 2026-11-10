import time

import pytest

from ark import loop, run
from test_loop import SLUG, state


@pytest.fixture(autouse=True)
def no_wait():
    yield
    loop._net_wait_until = 0.0


def test_net_hold_waits_until_the_network_answers(monkeypatch):
    up = [False]
    monkeypatch.setattr(loop, "_net_up", lambda: up[0])
    loop._net_wait_until = time.time() + 600
    assert loop.net_hold() == "wait"
    up[0] = True
    assert loop.net_hold() == "" and loop._net_wait_until == 0.0
    assert loop.net_hold() == ""


def test_net_hold_gives_up_after_the_limit(monkeypatch):
    monkeypatch.setattr(loop, "_net_up", lambda: False)
    loop._net_wait_until = time.time() - 1
    assert loop.net_hold() == "gave_up" and loop._net_wait_until == 0.0


def test_nothing_starts_during_the_wait_and_it_starts_once_the_network_is_back(feature, monkeypatch):
    feature()
    up = [False]
    monkeypatch.setattr(loop, "_net_up", lambda: up[0])
    loop._net_wait_until = time.time() + 600
    line, _, _ = loop.cycle()
    assert line.startswith("esperando red tras suspensi\u00f3n")
    st = state()
    assert st is None or not any(j["status"] == "working" for j in st["jobs"].values())
    up[0] = True
    loop.cycle()
    assert state()["jobs"]["shell"]["status"] == "working"


def test_receipts_are_consumed_during_the_wait_but_nothing_is_scheduled(feature, monkeypatch):
    feature()
    loop.cycle()                                   # started, outside any wait: no probe
    seen = []
    monkeypatch.setattr(loop, "consume_receipts", lambda c: seen.append("consume") or True)
    monkeypatch.setattr(loop, "schedule", lambda c, s: seen.append("schedule"))
    monkeypatch.setattr(loop, "_net_up", lambda: False)
    loop._net_wait_until = time.time() + 600
    loop.cycle()
    assert seen == ["consume"]


def test_giving_up_is_an_event_of_each_live_run(feature, monkeypatch):
    feature()
    loop.cycle()
    monkeypatch.setattr(loop, "_net_up", lambda: False)
    loop._net_wait_until = time.time() - 1
    loop.cycle()
    assert any("the network did not come back in 10 minutes; going on" in e for e in state()["events"])


def test_the_wait_makes_the_poll_cycle(monkeypatch, ark_home):
    loop._net_wait_until = time.time() + 600
    assert loop._retry_due() is True
