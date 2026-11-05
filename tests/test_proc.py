import json
import os
import signal
import sys
import time

from ark import paths, proc


def wait_receipt(d, name, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if name in proc.finished(d):
            return
        time.sleep(0.05)
    raise AssertionError(f"no receipt for {name}")


def py(code):
    return [sys.executable, "-c", code]


def test_receipt_reads_claude_result_line(tmp_path):
    line = json.dumps({"type": "result", "result": "hi\nSTATUS: DONE", "session_id": "s1",
                       "total_cost_usd": 0.5, "is_error": False})
    proc.start(tmp_path, "j.worker", py(f"print('noise'); print({line!r})"), cwd=tmp_path,
               env=dict(os.environ), minutes=1)
    assert proc.active(tmp_path) == ["j.worker"]
    wait_receipt(tmp_path, "j.worker")
    r = proc.consume(tmp_path, "j.worker")
    assert (r["exit"], r["result"], r["session"], r["usd"]) == (0, "hi\nSTATUS: DONE", "s1", 0.5)
    assert proc.active(tmp_path) == []
    assert list((tmp_path / "done").glob("j.worker-*.json")) and list((tmp_path / "done").glob("j.worker-*.log"))


def test_plain_command_gives_its_tail_and_exit(tmp_path):
    proc.start(tmp_path, "j.gate", ["sh", "-c", "echo red; exit 3"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.gate")
    r = proc.consume(tmp_path, "j.gate")
    assert r["exit"] == 3 and "red" in r["result"] and r["reason"] == ""


def test_failed_setup_skips_the_command(tmp_path):
    marker = tmp_path / "ran"
    proc.start(tmp_path, "j.worker", ["touch", str(marker)], cwd=tmp_path, env=dict(os.environ),
               minutes=1, pre=["exit 4"])
    wait_receipt(tmp_path, "j.worker")
    r = proc.consume(tmp_path, "j.worker")
    assert r["exit"] == 4 and r["reason"] == "setup failed: exit 4" and not marker.exists()


def test_pythonpath_does_not_leak_into_the_command(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc.start(tmp_path, "j.x", py("import os; print(os.environ.get('PYTHONPATH', '-'))"), cwd=tmp_path,
               env=env, minutes=1)
    wait_receipt(tmp_path, "j.x")
    assert proc.consume(tmp_path, "j.x")["result"].strip() == "-"


def test_over_time_is_stopped(tmp_path):
    proc.start(tmp_path, "j.worker", ["sleep", "30"], cwd=tmp_path, env=dict(os.environ), minutes=0.001)
    time.sleep(0.2)
    assert proc.overdue(tmp_path) == ["j.worker"]
    assert proc.reap(tmp_path) == ["j.worker"]
    r = proc.consume(tmp_path, "j.worker")
    assert r["reason"].startswith("timeout")


def test_died_without_receipt(tmp_path):
    info = proc.start(tmp_path, "j.worker", ["sleep", "30"], cwd=tmp_path, env=dict(os.environ), minutes=5)
    time.sleep(0.3)
    os.kill(info["pid"], signal.SIGKILL)          # the wrapper dies; no receipt is written
    time.sleep(0.2)
    try:
        assert proc.reap(tmp_path) == ["j.worker"]
        assert proc.consume(tmp_path, "j.worker")["reason"] == "died without a receipt"
    finally:
        os.killpg(info["pid"], signal.SIGKILL)    # the orphaned sleep


def test_dispatch_stop(ark_home):
    d = paths.run_dir("demo", "x")
    proc.start(d, "shell.worker", ["sleep", "30"], cwd=d.parent, env=dict(os.environ), minutes=5)
    time.sleep(0.3)
    assert proc.main(["stop", "demo/x", "shell"]) == 0
    assert proc.consume(d, "shell.worker")["reason"] == "stopped by Marco"
    assert proc.main(["stop", "demo/x", "shell"]) == 1
