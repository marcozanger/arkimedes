import json
import os
import time
from types import SimpleNamespace

import pytest

from ark import loop, paths, proc, run
from test_loop import bare, receipt, consume_one, SLUG


@pytest.fixture(autouse=True)
def no_wait():
    yield
    loop._net_wait_until = 0.0  # a wait left behind would make later cycles probe the real network


def write_proc(d, name, deadline, receipt_too=False):
    d.mkdir(parents=True, exist_ok=True)
    info = {"name": name, "pid": 4242, "cmd": "x", "cwd": "/", "start": 0, "deadline": deadline,
            "leader_start": 1, "leader_cmd": "-m ark.proc run", "meta": {}}
    (d / f"{name}.proc").write_text(json.dumps(info))
    if receipt_too:
        (d / f"{name}.receipt.json").write_text("{}")


def test_shift_deadlines_moves_only_running_processes(tmp_path):
    write_proc(tmp_path, "a.worker", 1000)
    write_proc(tmp_path, "b.gate", 1000, receipt_too=True)
    assert proc.shift_deadlines(tmp_path, 7200) == []
    assert json.loads((tmp_path / "a.worker.proc").read_text())["deadline"] == 8200
    assert json.loads((tmp_path / "b.gate.proc").read_text())["deadline"] == 1000


def test_an_unreadable_proc_keeps_its_deadline_and_is_named(tmp_path):
    (tmp_path / "a.worker.proc").write_text("not json")
    assert proc.shift_deadlines(tmp_path, 60) == ["a.worker: unreadable .proc"]
    assert (tmp_path / "a.worker.proc").read_text() == "not json"


def test_slept_between_ignores_short_and_negative_jumps():
    assert loop.slept_between((0, 0), (7200, 0)) == 7200
    assert loop.slept_between((0, 0), (59, 0)) == 0
    assert loop.slept_between((100, 0), (0, 0)) == 0
    assert loop.slept_between((0, 0), (3600, 3590)) == 0      # awake: both clocks moved


def test_shift_run_moves_every_clock_marks_live_processes_and_says_so(tmp_path, ark_home):
    c = bare(tmp_path, soft_deadline=1000, watch_from=500, not_before=0, holders_since=900, attempts=1)
    st = c.st
    st.update(status="running", closing_wait=2000)
    d = paths.run_dir("demo", SLUG)
    write_proc(d, "shell.worker", 3000)
    write_proc(d, "shell.intervene", 3000)
    write_proc(d, loop.DECISIONS, 3000)
    loop.shift_run(st, 7200, woke_at=9999)
    j = st["jobs"]["shell"]
    assert (j["soft_deadline"], j["watch_from"], j["holders_since"]) == (8200, 7700, 8100)
    assert j["not_before"] == 0                                   # absent or 0 stays as it is
    assert st["closing_wait"] == 9200
    assert j["woke_at"] == 9999
    assert j["slept_through"] == {"worker": 9999, "intervene": 9999}
    assert st["slept_through"] == 9999
    assert json.loads((d / "shell.worker.proc").read_text())["deadline"] == 10200
    assert any("the machine slept 2h00m: deadlines moved by that much" in e for e in st["events"])


def test_on_wake_saves_every_live_run_and_skips_finished_ones(monkeypatch, tmp_path, ark_home):
    live = bare(tmp_path, soft_deadline=1000).st
    live["status"] = "running"
    done = {**bare(tmp_path).st, "slug": "other", "status": "done"}
    states = {("demo", SLUG): live, ("demo", "other"): done}
    saved = []
    monkeypatch.setattr(loop, "approvals", lambda: [{"repo": r, "slug": s} for r, s in states])
    monkeypatch.setattr(loop, "_load", lambda r, s: states[(r, s)])
    monkeypatch.setattr(run, "save", lambda st: saved.append(st["slug"]))
    loop.on_wake(7200, woke_at=9999)
    assert saved == [SLUG]
    assert live["jobs"]["shell"]["soft_deadline"] == 8200
    assert loop._net_wait_until == 9999 + loop.NET_WAIT_MAX


def test_a_worker_silent_through_the_sleep_is_not_stalled(tmp_path):
    from ark import claude
    log = tmp_path / "w.log"
    log.write_text("x")
    old = time.time() - 3 * 3600
    os.utime(log, (old, old))
    now = time.time()
    j = {"tier": "T1", "soft_deadline": now + 3600, "watch_from": old, "token_factor": 1, "woke_at": now - 60}
    assert loop._anomaly(j, log, now) is None
    j.pop("woke_at")
    assert "no output" in loop._anomaly(j, log, now)


def test_polling_calls_on_wake_once_per_sleep(monkeypatch, ark_home):
    samples = iter([(0, 0), (7200, 10), (7210, 20)])
    woke, sleeps = [], []
    monkeypatch.setattr(loop, "clocks", lambda: next(samples))
    monkeypatch.setattr(loop, "on_wake", lambda s, w: woke.append((s, w)) or [])
    monkeypatch.setattr(loop, "signature", lambda: "same")
    monkeypatch.setattr(loop, "_retry_due", lambda: False)
    monkeypatch.setattr(loop, "cycle", lambda: ("idle", [], False))

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        loop._polling(False)
    assert woke == [(7190, 7200)]
