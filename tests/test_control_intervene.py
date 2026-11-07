import os
import time

from control_helpers import SLUG, change_state, drive, job_status, settled, state

from ark import claude, loop, paths, proc


def worker_log():
    return paths.run_dir("demo", SLUG) / "shell.worker.log"


def holding(feature, monkeypatch, tmp_path, **env):
    go = tmp_path / "go"
    monkeypatch.setenv("FAKE_HOLD", str(go))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    feature()
    drive(lambda: job_status("shell") == "working")
    return go


def events():
    return "\n".join(state()["events"])


def test_the_process_gets_twice_the_tier_time(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path)
    info = proc.job_processes(paths.run_dir("demo", SLUG), "shell")[0]
    assert abs((info["deadline"] - info["start"]) - 2 * claude.TIER_MINUTES["T1"] * 60) < 5
    j = state()["jobs"]["shell"]
    assert abs(j["soft_deadline"] - time.time() - claude.TIER_MINUTES["T1"] * 60) < 60
    go.touch()
    drive(settled)


def test_anomalies_are_named(tmp_path):
    log = tmp_path / "w.log"
    log.write_text("")
    now = time.time()
    fine = {"tier": "T1", "soft_deadline": now + 100, "watch_from": now, "token_factor": 1}
    assert loop._anomaly(fine, log, now) is None
    assert "over its time" in loop._anomaly({**fine, "soft_deadline": now - 1}, log, now)
    old = now - (claude.STALL_MINUTES + 1) * 60
    os.utime(log, (old, old))
    assert "no output" in loop._anomaly({**fine, "watch_from": old}, log, now)
    assert loop._anomaly(fine, log, now) is None   # a fresh watch_from: the clock restarted


def test_tokens_over_the_tier_limit_are_an_anomaly(tmp_path):
    log = tmp_path / "w.log"
    log.write_text('{"type": "assistant", "message": {"id": "m", "content": [], "usage": {"output_tokens": 500000}}}\n')
    now = time.time()
    j = {"tier": "T1", "soft_deadline": now + 100, "watch_from": now, "token_factor": 1}
    assert "tokens" in loop._anomaly(j, log, now)
    assert loop._anomaly({**j, "token_factor": 1.5}, log, now) is None


def test_a_worker_past_its_soft_limit_is_noticed(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path)
    change_state(shell={"soft_deadline": time.time() - 1})
    assert loop._retry_due()
    drive(lambda: "over its time" in events())
    go.touch()
    drive(settled)


def test_a_worker_inside_its_limits_is_left_alone(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path)
    for _ in range(3):
        loop.cycle()
    assert "looks wrong" not in events()
    assert not loop._retry_due()
    go.touch()
    drive(settled)
