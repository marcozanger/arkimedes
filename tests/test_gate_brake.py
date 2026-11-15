import json
import os
from types import SimpleNamespace

import pytest
from test_loop import bare

from ark import claude, gitops, loop

LIMITS = {"writers_per_repo": 1, "writers_total": 2, "heavy_gates": 1, "load_max": 4, "load_wait_max": 900}


def st_():
    return {"repo": "demo", "slug": "x", "events": []}


@pytest.fixture
def clock(monkeypatch):
    loop._load_wait.clear()
    t = SimpleNamespace(now=1000.0, load=9.0)
    monkeypatch.setattr(loop.time, "monotonic", lambda: t.now)
    monkeypatch.setattr(os, "getloadavg", lambda: (t.load, 0, 0))
    return t


def test_gate_waits_while_the_machine_is_busy_and_says_so_once(clock):
    st, s = st_(), loop.Slots([], LIMITS)
    for _ in range(5):
        assert not s.gate(False, st, "j")
        clock.now += 15
    assert len(st["events"]) == 1 and "waits for the machine" in st["events"][0]


def test_gate_starts_when_the_load_is_below_the_max(clock):
    clock.load = 3.9
    st = st_()
    assert loop.Slots([], LIMITS).gate(False, st, "j") and st["events"] == []


def test_a_gate_starts_anyway_after_load_wait_max(clock):
    st, s = st_(), loop.Slots([], LIMITS)
    assert not s.gate(False, st, "j")
    clock.now += 899
    assert not s.gate(False, st, "j")
    clock.now += 2
    assert s.gate(False, st, "j")
    assert len(st["events"]) == 2 and "starts after waiting" in st["events"][1]
    assert not loop._load_wait  # the next gate of this job waits afresh


def test_a_gate_that_gave_up_waiting_adds_no_events_while_heavy_holds_it(clock):
    st, s = st_(), loop.Slots([], {**LIMITS, "load_wait_max": 10})
    s.heavy = 1
    s.gate(True, st, "j")
    clock.now += 20
    assert not s.gate(True, st, "j")  # the heavy slot is taken, not the load
    assert not s.gate(True, st, "j")
    assert len(st["events"]) == 2


def test_load_max_zero_disables_the_brake(clock):
    st = st_()
    assert loop.Slots([], {**LIMITS, "load_max": 0}).gate(False, st, "j") and st["events"] == []


def test_the_brake_does_not_take_a_heavy_slot(clock):
    s = loop.Slots([], LIMITS)
    assert not s.gate(True, st_(), "j") and s.heavy == 0


def test_without_a_run_there_is_no_brake(clock):
    assert loop.Slots([], LIMITS).gate(False)


def test_load_dropping_resets_the_wait(clock):
    st, s = st_(), loop.Slots([], LIMITS)
    assert not s.gate(False, st, "j")
    clock.load = 1.0
    assert s.gate(False, st, "j")


def test_a_job_waits_in_next_step(clock, tmp_path):
    c = bare(tmp_path, status="needs_gate")
    j = c.st["jobs"]["shell"]
    assert loop._next_step(c, "shell", j, loop.Slots([c.st], LIMITS)) is None
    clock.load = 1.0
    assert loop._next_step(c, "shell", j, loop.Slots([c.st], LIMITS)) is not None


def ctx():
    return SimpleNamespace(st={"repo": "demo"}, repo="/nowhere")


@pytest.fixture
def env_(monkeypatch):
    monkeypatch.setattr(gitops, "origin_url", lambda r: "https://example.invalid/x.git")
    monkeypatch.setattr(claude, "with_account", lambda e, r: e)
    for k in ("VITEST_MAX_WORKERS", "PYTEST_XDIST_AUTO_NUM_WORKERS", "ARK_TEST_WORKERS"):
        monkeypatch.delenv(k, raising=False)


def test_gate_env_sets_the_worker_cap(env_, ark_home):
    e = loop.gate_env(ctx(), "gate")
    assert e["ARK_TEST_WORKERS"] == e["VITEST_MAX_WORKERS"] == e["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "4"
    assert e["ARK_ROLE"] == "gate"


def test_gate_env_follows_the_workspace(env_, ark_home):
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "limits": {"gate_workers": 2}}))
    assert loop.gate_env(ctx(), "integrate")["ARK_TEST_WORKERS"] == "2"


def test_gate_env_keeps_values_already_set(env_, ark_home, monkeypatch):
    monkeypatch.setenv("VITEST_MAX_WORKERS", "1")
    e = loop.gate_env(ctx(), "gate")
    assert e["VITEST_MAX_WORKERS"] == "1" and e["ARK_TEST_WORKERS"] == "4"
