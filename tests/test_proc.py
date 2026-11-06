import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ark import paths, proc

# Holds every process a test started by itself, so a failing test cannot leave one running.
_mine: list[subprocess.Popen] = []
_extra_pids: list[int] = []

IGNORE_TERM = ("import os, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
               "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(20)")


@pytest.fixture(autouse=True)
def only_our_children():
    yield
    _mine.extend(proc._children.values())
    proc._children.clear()
    for p in _mine:
        if p.poll() is None:              # unreaped child of this test: the pid is still ours
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:               # its group already emptied; only the zombie is left to reap
                pass
            p.wait()
    _mine.clear()
    for pid in _extra_pids:               # read from a pid file its own child of this test wrote
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _extra_pids.clear()


def restart():
    """What a new ark-loop sees: no Popen objects, only the files."""
    _mine.extend(proc._children.values())
    proc._children.clear()


def bystander(*extra):
    """A process outside Arkimedes, leader of its own group: what a reused pid points at."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)", *extra], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _mine.append(p)
    return p


def dead_wrapper_proc(d, name):
    """A real `.proc` whose wrapper is gone and left no receipt: the state a reboot leaves."""
    info = proc.start(d, name, ["true"], cwd=d, env=dict(os.environ), minutes=5)
    wait_receipt(d, name)
    proc._children.pop(str(Path(d) / name)).wait()
    (Path(d) / f"{name}.receipt.json").unlink()
    return json.loads((Path(d) / f"{name}.proc").read_text())


def reuse_pid(d, name, by, **changes):
    info = dead_wrapper_proc(d, name)
    (Path(d) / f"{name}.proc").write_text(json.dumps({**info, "pid": by.pid, **changes}))


def read_pid(path, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            pid = int(path.read_text())
            _extra_pids.append(pid)
            return pid
        time.sleep(0.02)
    raise AssertionError(f"no pid in {path}")


def running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_dead(pid, seconds=None):
    end = time.time() + (proc.KILL_WAIT + 2 if seconds is None else seconds)
    while time.time() < end:
        if not running(pid):
            return
        time.sleep(0.05)
    raise AssertionError(f"process {pid} is still running")


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


@pytest.mark.parametrize("lines,session,spoke", [
    (["Could not reach the model"], None, False),
    (['{"type":"system","subtype":"init","session_id":"s1"}'], "s1", False),
    (['{"type":"system","subtype":"init","session_id":"s1"}', '{"type":"assistant"}', "killed"], "s1", True),
    (['{"type":"something new"}'], None, True),                                 # not known to be nothing: counts as work
    (['{"type":"assistant"'], None, False),                                     # half a line, cut off
])
def test_output_with_no_result_line_tells_what_the_model_did(lines, session, spoke):
    r = proc.parse_output("\n".join(lines))
    assert r["is_error"] is None and (r["session"], r["spoke"]) == (session, spoke)


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
    pid_file = tmp_path / "pid"
    proc.start(tmp_path, "j.worker", ["sh", "-c", f"echo $$ > {pid_file}; exec sleep 20"], cwd=tmp_path,
               env=dict(os.environ), minutes=0.001)
    pid = read_pid(pid_file)
    time.sleep(0.2)
    assert proc.overdue(tmp_path) == ["j.worker"]
    assert proc.reap(tmp_path) == ["j.worker"]
    r = proc.consume(tmp_path, "j.worker")
    assert r["reason"].startswith("timeout")
    wait_dead(pid)


def test_died_without_receipt(tmp_path):
    pid_file = tmp_path / "pid"
    info = proc.start(tmp_path, "j.worker", ["sh", "-c", f"echo $$ > {pid_file}; exec sleep 20"], cwd=tmp_path,
                      env=dict(os.environ), minutes=5)
    pid = read_pid(pid_file)
    os.kill(info["pid"], signal.SIGKILL)          # the wrapper dies; no receipt is written
    time.sleep(0.2)
    assert proc.reap(tmp_path) == ["j.worker"]
    assert proc.consume(tmp_path, "j.worker")["reason"] == "died without a receipt"
    wait_dead(pid)                                # nothing keeps running unseen


def test_dispatch_stop(ark_home):
    d = paths.run_dir("demo", "x")
    pid_file = d.parent / "pid"
    d.mkdir(parents=True)
    proc.start(d, "shell.worker", ["sh", "-c", f"echo $$ > {pid_file}; exec sleep 20"], cwd=d.parent,
               env=dict(os.environ), minutes=5)
    pid = read_pid(pid_file)
    assert proc.main(["stop", "demo/x", "shell"]) == 0
    assert proc.consume(d, "shell.worker")["reason"] == "stopped by Marco"
    wait_dead(pid)
    assert proc.main(["stop", "demo/x", "shell"]) == 1


def stop_cli(ark_home, capsys, job="j"):
    code = proc.main(["stop", "demo/x", job])
    return code, capsys.readouterr().out


def test_dispatch_stop_with_survivors_after_signalling_says_so(ark_home, capsys, monkeypatch):
    d = paths.run_dir("demo", "x")
    _, pid = stubborn(d)
    monkeypatch.setattr(proc, "_group_ended", lambda pgid, seconds: False)
    code, out = stop_cli(ark_home, capsys)
    assert code == 0
    assert "detenido: j.worker" in out and "quedan" in out
    assert "ninguna señal" not in out and "nada corriendo" not in out


def test_dispatch_stop_with_survivors_of_a_dead_wrapper_does_not_say_nothing_runs(ark_home, capsys, monkeypatch):
    monkeypatch.setattr(proc, "KILL_WAIT", 0.3)
    d = paths.run_dir("demo", "x")
    pid_file = d / "pid"
    d.mkdir(parents=True)
    orphan_maker = ("import os, sys, time\n"
                    "if os.fork() == 0:\n"
                    "    open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(20)\n")
    leader = subprocess.Popen([sys.executable, "-c", orphan_maker, str(pid_file)], start_new_session=True,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    member = read_pid(pid_file)
    leader.wait()
    info = dead_wrapper_proc(d, "j.worker")
    (d / "j.worker.proc").write_text(json.dumps({**info, "pid": leader.pid}))
    code, out = stop_cli(ark_home, capsys)
    assert code == 1
    assert "quedan" in out and "nada corriendo" not in out and "no se mandó ninguna señal" in out
    assert running(member)


def test_dispatch_stop_with_an_undecided_identity_does_not_say_nothing_runs(ark_home, capsys, monkeypatch):
    d = paths.run_dir("demo", "x")
    info = proc.start(d, "j.worker", ["sleep", "20"], cwd=d, env=dict(os.environ), minutes=5)
    restart()
    monkeypatch.setattr(proc, "_describe", lambda pid: proc.UNKNOWN)
    code, out = stop_cli(ark_home, capsys)
    assert code == 1
    assert "nada corriendo" not in out and "no se pudo verificar" in out and "j.worker" in out
    assert running(info["pid"])


# ---- identity: a signal reaches only the group this runner started ------------------------------

def test_a_live_wrapper_is_recognised_after_a_restart_in_any_locale_or_timezone(tmp_path, monkeypatch):
    info = proc.start(tmp_path, "j.worker", ["sleep", "20"], cwd=tmp_path, env=dict(os.environ), minutes=5)
    restart()
    assert proc._alive(tmp_path, "j.worker", info["pid"]) is True
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    monkeypatch.setenv("LC_ALL", "es_AR.UTF-8")
    assert proc._alive(tmp_path, "j.worker", info["pid"]) is True
    assert proc.reap(tmp_path) == []
    assert proc.stop(tmp_path, "j.worker", "stopped by Marco") is True
    assert proc.consume(tmp_path, "j.worker")["reason"] == "stopped by Marco"


def test_a_live_wrapper_is_recognised_with_a_non_ascii_run_dir(tmp_path):
    d = tmp_path / "diseño-ñandú-日本"
    info = proc.start(d, "j.worker", ["sleep", "20"], cwd=tmp_path, env=dict(os.environ), minutes=5)
    restart()
    assert proc._alive(d, "j.worker", info["pid"]) is True
    assert proc.reap(d) == []
    assert proc.stop(d, "j.worker", "stopped by Marco") is True
    assert proc.consume(d, "j.worker")["reason"] == "stopped by Marco"


def test_ps_dates_parse_in_other_locales(monkeypatch):
    monkeypatch.setenv("LC_ALL", "es_AR.UTF-8")
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    seen = proc._describe(os.getpid())
    assert not isinstance(seen, str) and abs(seen[0] - time.time()) < 86400 * 365


def reused_variants(tmp_path, name, variant):
    """Same start second with another command, or the same command with another start."""
    if variant == "other command":
        by = bystander()
        info = dead_wrapper_proc(tmp_path, name)
        start = proc._describe(by.pid)[0]
        (tmp_path / f"{name}.proc").write_text(json.dumps({**info, "pid": by.pid, "leader_start": start}))
        return by
    info = dead_wrapper_proc(tmp_path, name)
    by = bystander(info["leader_cmd"])
    for _ in range(100):                  # until ps shows the command, not the moment before exec
        seen = proc._describe(by.pid)
        if not isinstance(seen, str) and info["leader_cmd"] in seen[1]:
            break
        time.sleep(0.02)
    (tmp_path / f"{name}.proc").write_text(
        json.dumps({**info, "pid": by.pid, "leader_start": info["leader_start"] - 1000}))
    return by


@pytest.mark.parametrize("variant", ["other command", "other start"])
def test_reused_pid_counts_as_died_and_gets_no_signal(tmp_path, variant):
    by = reused_variants(tmp_path, "j.worker", variant)
    info = proc._read(tmp_path / "j.worker.proc")
    assert proc._alive(tmp_path, "j.worker", info["pid"]) is False
    t0 = time.time()
    assert proc.reap(tmp_path) == ["j.worker"]
    assert time.time() - t0 < 2           # the old group cannot exist: nothing to wait for
    assert proc.consume(tmp_path, "j.worker")["reason"] == "died without a receipt"
    time.sleep(0.2)
    assert by.poll() is None


def test_reused_pid_past_its_deadline_gets_no_signal(tmp_path):
    by = bystander()
    reuse_pid(tmp_path, "j.worker", by, deadline=time.time() - 60)
    assert proc.reap(tmp_path) == ["j.worker"]
    assert proc.consume(tmp_path, "j.worker")["reason"] == "died without a receipt"
    time.sleep(0.2)
    assert by.poll() is None


def test_stop_on_a_reused_pid_sends_nothing_and_records_the_death(tmp_path):
    by = bystander()
    reuse_pid(tmp_path, "j.worker", by)
    assert proc.stop(tmp_path, "j.worker", "stopped by Marco") is False
    r = proc.consume(tmp_path, "j.worker")
    assert (r["reason"], r["cause"]) == ("stopped by Marco", "died without a receipt")  # it still counts as a stop
    time.sleep(0.2)
    assert by.poll() is None


def test_a_group_without_its_leader_that_cannot_be_verified_gets_no_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "KILL_WAIT", 0.3)
    pid_file = tmp_path / "pid"
    orphan_maker = ("import os, sys, time\n"
                    "if os.fork() == 0:\n"
                    "    open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(20)\n")
    leader = subprocess.Popen([sys.executable, "-c", orphan_maker, str(pid_file)], start_new_session=True,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    member = read_pid(pid_file)
    leader.wait()
    info = dead_wrapper_proc(tmp_path, "j.worker")
    (tmp_path / "j.worker.proc").write_text(json.dumps({**info, "pid": leader.pid}))
    assert proc.reap(tmp_path) == ["j.worker"]
    r = proc.consume(tmp_path, "j.worker")
    assert "not signalled" in r["reason"] and r["survivors"]
    assert running(member)


def test_when_identity_cannot_be_read_nothing_is_signalled_or_decided(tmp_path, monkeypatch):
    info = proc.start(tmp_path, "j.worker", ["sleep", "20"], cwd=tmp_path, env=dict(os.environ), minutes=0.001)
    time.sleep(0.2)
    restart()
    monkeypatch.setattr(proc, "_describe", lambda pid: proc.UNKNOWN)
    assert proc._alive(tmp_path, "j.worker", info["pid"]) is True
    assert proc.reap(tmp_path) == []
    assert proc.stop(tmp_path, "j.worker", "stopped by Marco") is False
    assert proc.finished(tmp_path) == [] and running(info["pid"])


def test_a_start_whose_identity_cannot_be_read_runs_nothing_and_fails(tmp_path, monkeypatch):
    marker = tmp_path / "ran"
    monkeypatch.setattr(proc, "_describe", lambda pid: proc.UNKNOWN)
    proc.start(tmp_path, "j.worker", ["touch", str(marker)], cwd=tmp_path, env=dict(os.environ), minutes=5)
    assert proc.finished(tmp_path) == ["j.worker"]
    r = proc.consume(tmp_path, "j.worker")
    assert r["exit"] == -1 and "cannot verify" in r["reason"]
    time.sleep(0.3)
    assert not marker.exists()


def test_a_wrapper_whose_starter_died_before_recording_it_runs_nothing(tmp_path):
    marker = tmp_path / "ran"
    env = {**os.environ, "PYTHONPATH": str(paths.code_dir() / "lib")}
    subprocess.run([sys.executable, "-m", "ark.proc", "run", str(tmp_path), "j.worker", "--cwd", str(tmp_path),
                    "--", "touch", str(marker)], env=env, stdin=subprocess.DEVNULL, check=True, timeout=30)
    assert not marker.exists() and proc.active(tmp_path) == []
    assert not (tmp_path / "j.worker.receipt.json").exists()


# ---- escalation: SIGKILL for a process that survives SIGTERM ------------------------------------

def stubborn(d, name="j.worker", minutes=5):
    pid_file = Path(d) / f"{name}.pid"
    info = proc.start(d, name, [sys.executable, "-c", IGNORE_TERM, str(pid_file)], cwd=d, env=dict(os.environ),
                      minutes=minutes)
    return info, read_pid(pid_file)


@pytest.mark.parametrize("restarted", [False, True])
def test_stop_kills_a_child_that_ignores_sigterm(tmp_path, restarted):
    _, pid = stubborn(tmp_path)
    if restarted:
        restart()
    t0 = time.time()
    assert proc.stop(tmp_path, "j.worker", "stopped by Marco") is True
    assert time.time() - t0 <= proc.KILL_WAIT + 1
    wait_dead(pid, 1)
    r = proc.consume(tmp_path, "j.worker")
    assert r["reason"] == "stopped by Marco" and not r.get("survivors")


def test_timeout_kills_a_child_that_ignores_sigterm_after_a_restart(tmp_path):
    _, pid = stubborn(tmp_path, minutes=0.002)
    restart()
    time.sleep(0.2)
    t0 = time.time()
    assert proc.reap(tmp_path) == ["j.worker"]
    assert time.time() - t0 <= proc.KILL_WAIT + 1
    wait_dead(pid, 1)
    assert proc.consume(tmp_path, "j.worker")["reason"].startswith("timeout")


def test_a_stop_from_another_process_does_not_wait_for_the_starter_to_reap(tmp_path):
    info = proc.start(tmp_path, "j.worker", ["sleep", "20"], cwd=tmp_path, env=dict(os.environ), minutes=5)
    time.sleep(0.2)
    starter_keeps = proc._children.pop(str(tmp_path / "j.worker"))   # its wrapper stays our zombie
    _mine.append(starter_keeps)
    t0 = time.time()
    assert proc.stop(tmp_path, "j.worker", "stopped by Marco") is True
    assert time.time() - t0 < proc.TERM_GRACE
    assert proc._describe(info["pid"]) == proc.GONE


# ---- a dead wrapper leaves nothing behind -------------------------------------------------------

@pytest.mark.parametrize("restarted", [False, True])
def test_a_dead_wrapper_takes_its_command_with_it_even_with_no_loop_running(tmp_path, restarted):
    info, pid = stubborn(tmp_path)        # the worst case: the command also ignores SIGTERM
    if restarted:
        restart()
    os.kill(info["pid"], signal.SIGKILL)
    wait_dead(pid)                        # reap was never called
    assert proc.reap(tmp_path) == ["j.worker"]
    assert proc.consume(tmp_path, "j.worker")["reason"] == "died without a receipt"


def test_reap_reports_a_dead_wrapper_only_once_its_group_is_gone(tmp_path):
    info, pid = stubborn(tmp_path)
    os.kill(info["pid"], signal.SIGKILL)
    proc._children[str(tmp_path / "j.worker")].wait()
    assert proc.reap(tmp_path) == ["j.worker"]
    assert not running(pid)               # a retry cannot run next to the old worker


def test_background_jobs_do_not_outlive_a_normal_exit(tmp_path):
    pid_file = tmp_path / "pid"
    proc.start(tmp_path, "j.gate", ["sh", "-c", f"sleep 20 & echo $! > {pid_file}; exit 0"], cwd=tmp_path,
               env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.gate")
    pid = read_pid(pid_file)
    assert proc.consume(tmp_path, "j.gate")["exit"] == 0
    wait_dead(pid)


def test_a_wrapper_outside_its_own_group_sweeps_nothing(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(paths.code_dir() / "lib")}
    script = ('echo g | "$0" -m ark.proc run "$1" j.x --cwd "$1" -- true; sleep 0.5; echo ok > "$1/survived"')
    group = subprocess.Popen(["sh", "-c", script, sys.executable, str(tmp_path)], env=env, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _mine.append(group)
    group.wait(timeout=30)
    assert (tmp_path / "survived").exists()


# ---- consume: a crash at any step keeps the real result -----------------------------------------

class Crash(BaseException):
    pass


@pytest.mark.parametrize("crash_at", [1, 2, 3, 4])
def test_a_crash_inside_consume_keeps_a_done(tmp_path, monkeypatch, crash_at):
    proc.start(tmp_path, "j.worker", ["sh", "-c", "echo 'STATUS: DONE'"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.worker")
    calls = {"n": 0}
    real_replace, real_unlink = Path.replace, Path.unlink

    def step(real):
        def wrapped(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == crash_at:
                raise Crash
            return real(self, *a, **k)
        return wrapped

    with monkeypatch.context() as m:
        m.setattr(Path, "replace", step(real_replace))
        m.setattr(Path, "unlink", step(real_unlink))
        with pytest.raises(Crash):
            proc.consume(tmp_path, "j.worker")
    restart()

    assert proc.reap(tmp_path) == []
    if "j.worker" in proc.finished(tmp_path):
        r = proc.consume(tmp_path, "j.worker")
        assert r["exit"] == 0 and "STATUS: DONE" in r["result"] and r["reason"] == ""
    else:
        assert crash_at == 4              # past the commit point: it is forgotten, and archived
    assert proc.active(tmp_path) == [] and proc.reap(tmp_path) == []
    archived = [json.loads(f.read_text()) for f in (tmp_path / "done").glob("j.worker-*.json")]
    assert len(archived) == 1 and archived[0]["exit"] == 0 and "STATUS: DONE" in archived[0]["result"]
    assert len(list((tmp_path / "done").glob("j.worker-*.log"))) == 1


def test_consume_without_a_receipt_changes_nothing(tmp_path):
    proc.start(tmp_path, "j.worker", ["sh", "-c", "exit 0"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.worker")
    assert proc.consume(tmp_path, "j.worker")["exit"] == 0
    assert proc.consume(tmp_path, "j.worker") is None
    assert len(list((tmp_path / "done").glob("*.json"))) == 1

    proc.start(tmp_path, "j.gate", ["sleep", "20"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    assert proc.consume(tmp_path, "j.gate") is None
    assert proc.active(tmp_path) == ["j.gate"]


@pytest.mark.parametrize("content", ["[1]", "{not json", "", '{"exit": 0}', '{"exit": 0, "end": NaN}',
                                     '{"exit": 0, "end": Infinity}', '{"exit": 0, "end": -Infinity}'])
def test_a_bad_receipt_is_a_failure_with_every_key(tmp_path, content):
    proc.start(tmp_path, "j.worker", ["true"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.worker")
    (tmp_path / "j.worker.receipt.json").write_text(content)
    r = proc.consume(tmp_path, "j.worker")
    assert r["reason"] == "unreadable receipt"
    assert {"exit", "reason", "result", "usd", "session", "is_error", "end", "meta"} <= set(r)


# ---- a corrupt .proc --------------------------------------------------------------------------

@pytest.mark.parametrize("make", [
    lambda by: "",
    lambda by: '{"pid": ' + str(by),
    lambda by: "[]",
    lambda by: json.dumps({"pid": by}),
    lambda by: json.dumps({"pid": by, "deadline": 0}),
    lambda by: json.dumps({"pid": str(by), "deadline": 0, "leader_start": 0, "leader_cmd": "x"}),
    lambda by: json.dumps({"pid": 0, "deadline": 0, "leader_start": 0, "leader_cmd": "x"}),
    lambda by: json.dumps({"pid": True, "deadline": 0, "leader_start": 0, "leader_cmd": "x"}),
    lambda by: json.dumps({"pid": by, "deadline": 0, "leader_start": 0, "leader_cmd": ""}),
    lambda by: '{"pid": %d, "deadline": NaN, "leader_start": 0, "leader_cmd": "x"}' % by,
])
def test_a_corrupt_proc_resolves_to_a_failure_with_no_signal(tmp_path, make):
    by = bystander()
    (tmp_path / "j.worker.proc").write_text(make(by.pid))
    assert proc._read(tmp_path / "j.worker.proc") is None   # the loop's fingerprint reads it this way
    assert proc.overdue(tmp_path) == []
    assert proc.reap(tmp_path) == ["j.worker"]
    assert proc.finished(tmp_path) == ["j.worker"]
    r = proc.consume(tmp_path, "j.worker")
    assert r["exit"] == -1 and "unreadable .proc" in r["reason"] and r["meta"] == {}
    assert proc.active(tmp_path) == []
    time.sleep(0.2)
    assert by.poll() is None


def test_a_stop_of_a_corrupt_proc_sends_no_signal_and_still_counts(tmp_path):
    by = bystander()
    (tmp_path / "j.worker.proc").write_text(json.dumps({"pid": by.pid}))
    assert proc.stop(tmp_path, "j.worker", "stopped by Marco") is False
    assert proc.reap(tmp_path) == ["j.worker"]
    r = proc.consume(tmp_path, "j.worker")
    assert r["reason"] == "stopped by Marco" and "unreadable .proc" in r["cause"]
    time.sleep(0.2)
    assert by.poll() is None


def test_a_wrapper_does_not_write_a_receipt_for_a_process_that_is_no_longer_its_own(tmp_path):
    proc.start(tmp_path, "j.worker", ["sleep", "0.8"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    (tmp_path / "j.worker.proc").write_text("")
    assert proc.reap(tmp_path) == ["j.worker"]
    proc.consume(tmp_path, "j.worker")
    time.sleep(1.5)                       # the wrapper ends meanwhile and finds no .proc of its own
    assert not (tmp_path / "j.worker.receipt.json").exists()


# ---- small ones -------------------------------------------------------------------------------

def test_a_receipt_is_never_overwritten(tmp_path):
    proc._receipt(tmp_path, "j.worker", 0, "")
    proc._receipt(tmp_path, "j.worker", -15, "timeout: over its time limit")
    assert json.loads((tmp_path / "j.worker.receipt.json").read_text())["exit"] == 0


def test_concurrent_atomic_writes_do_not_collide(tmp_path):
    errors = []

    def write():
        try:
            for i in range(50):
                proc._atomic(tmp_path / "x.json", {"i": i})
        except OSError as e:
            errors.append(e)

    threads = [threading.Thread(target=write) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []


@pytest.mark.parametrize("target", ["../../outside/x", "/abs/x", "demo/../x"])
def test_dispatch_stop_rejects_a_path_outside_runs(ark_home, target):
    assert proc.main(["stop", target, "shell"]) == 2


def test_setup_with_a_bad_cwd_keeps_the_reason(tmp_path):
    proc.start(tmp_path, "j.worker", ["true"], cwd=tmp_path / "missing", env=dict(os.environ), minutes=1, pre=["true"])
    wait_receipt(tmp_path, "j.worker")
    r = proc.consume(tmp_path, "j.worker")
    assert r["exit"] == 127 and r["reason"].startswith("cannot start")


# ---- cwd_holders: who sits in a folder ----------------------------------------------------------

def sitter(cwd):
    p = subprocess.Popen(["sleep", "20"], cwd=cwd, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _mine.append(p)
    return p


def wait_holders(path, pid, want=True):
    end = time.time() + 5
    while time.time() < end:
        if (pid in proc.cwd_holders(path)) == want:
            return True
        time.sleep(0.05)
    return False


def test_cwd_holders_finds_a_process_in_the_folder_or_inside_it(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    inside, deep = sitter(tmp_path), sitter(tmp_path / "a" / "b")
    assert wait_holders(tmp_path, inside.pid) and wait_holders(tmp_path, deep.pid)


def test_cwd_holders_ignores_a_sibling_with_the_same_prefix(tmp_path):
    (tmp_path / "w").mkdir()
    (tmp_path / "w-2").mkdir()
    sibling = sitter(tmp_path / "w-2")
    assert wait_holders(tmp_path / "w-2", sibling.pid)
    assert sibling.pid not in proc.cwd_holders(tmp_path / "w")


def test_cwd_holders_resolves_symlinks(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    p = sitter(tmp_path / "real")
    assert wait_holders(tmp_path / "link", p.pid)


def test_cwd_holders_is_empty_for_an_empty_folder(tmp_path):
    (tmp_path / "empty").mkdir()
    assert proc.cwd_holders(tmp_path / "empty") == []


def test_cwd_holders_forgets_a_process_that_left(tmp_path):
    p = sitter(tmp_path)
    assert wait_holders(tmp_path, p.pid)
    p.kill()
    p.wait()
    assert wait_holders(tmp_path, p.pid, want=False)


def test_cwd_holders_raises_when_lsof_is_missing_or_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "LSOF_ENV", {"PATH": "/nonexistent"})
    with pytest.raises(proc.CwdHoldersError):
        proc.cwd_holders(tmp_path)


def test_cwd_holders_raises_on_a_timeout(tmp_path, monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("lsof", 1)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(proc.CwdHoldersError):
        proc.cwd_holders(tmp_path)


@pytest.mark.parametrize("code,out,err", [(1, "", "lsof: boom"), (2, "", ""), (0, "", ""), (1, "pgarbage\n", "")])
def test_cwd_holders_does_not_read_a_failure_as_nothing(tmp_path, monkeypatch, code, out, err):
    r = subprocess.CompletedProcess(["lsof"], code, out, err)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: r)
    with pytest.raises(proc.CwdHoldersError):
        proc.cwd_holders(tmp_path)


@pytest.mark.parametrize("name", ["shell.gate", "_decisions.tick"])
def test_output_does_not_go_through_a_planted_log_link(tmp_path, name):
    d, victim = tmp_path / "run", tmp_path / "victim"
    d.mkdir()
    victim.write_text("mine")
    (d / f"{name}.log").symlink_to(victim)
    proc.start(d, name, ["sh", "-c", "echo gate-output"], cwd=d, env=dict(os.environ), minutes=1)
    wait_receipt(d, name)
    assert victim.read_text() == "mine"
    assert not (d / f"{name}.log").is_symlink() and (d / f"{name}.log").read_text() == "gate-output\n"
    assert "gate-output" in proc.consume(d, name)["result"]


def test_output_does_not_create_the_target_of_a_dangling_log_link(tmp_path):
    d, victim = tmp_path / "run", tmp_path / "victim"
    d.mkdir()
    (d / "shell.gate.log").symlink_to(victim)
    proc.start(d, "shell.gate", ["sh", "-c", "echo gate-output"], cwd=d, env=dict(os.environ), minutes=1)
    wait_receipt(d, "shell.gate")
    assert not victim.exists()


def test_output_does_not_go_into_a_planted_hard_link(tmp_path):
    d, victim = tmp_path / "run", tmp_path / "victim"
    d.mkdir()
    victim.write_text("mine")
    os.link(victim, d / "shell.gate.log")
    proc.start(d, "shell.gate", ["sh", "-c", "echo gate-output"], cwd=d, env=dict(os.environ), minutes=1)
    wait_receipt(d, "shell.gate")
    assert victim.read_text() == "mine"


# ---- a stop from Marco wins, whoever gets to write the receipt ------------------------------------

STOPPED = "stopped by Marco"


def reap_during_the_wait(d, monkeypatch):
    """What the live loop does while a stop waits for the group: it sees the wrapper gone and reaps."""
    real = proc._group_ended

    def racing(pgid, seconds):
        monkeypatch.setattr(proc, "_group_ended", real)
        end = time.monotonic() + 5
        while proc._leader_state(d, "j.worker", proc._read(d / "j.worker.proc")) == proc.OURS:
            assert time.monotonic() < end
            time.sleep(0.02)
        assert proc.reap(d) == ["j.worker"]
        return real(pgid, seconds)
    monkeypatch.setattr(proc, "_group_ended", racing)


def test_a_reap_during_a_stop_leaves_it_a_stop(tmp_path, monkeypatch):
    proc.start(tmp_path, "j.worker", ["sleep", "20"], cwd=tmp_path, env=dict(os.environ), minutes=5)
    reap_during_the_wait(tmp_path, monkeypatch)
    assert proc.stop(tmp_path, "j.worker", STOPPED) is True
    assert proc.consume(tmp_path, "j.worker")["reason"] == STOPPED


def test_a_stop_after_the_process_ended_still_counts_until_its_receipt_is_consumed(ark_home, capsys):
    d = paths.run_dir("demo", "x")
    proc.start(d, "j.worker", ["true"], cwd=d, env=dict(os.environ), minutes=5)
    wait_receipt(d, "j.worker")
    code, out = stop_cli(ark_home, capsys)
    assert code == 0 and "detenido: j.worker" in out
    assert proc.consume(d, "j.worker")["reason"] == STOPPED


def test_a_stop_of_a_dead_wrapper_counts_and_says_so(ark_home, capsys):
    d = paths.run_dir("demo", "x")
    dead_wrapper_proc(d, "j.worker")
    code, out = stop_cli(ark_home, capsys)
    assert code == 0 and "detenido: j.worker" in out
    assert proc.consume(d, "j.worker")["reason"] == STOPPED


def test_a_stop_ps_could_not_verify_still_counts_when_the_process_ends(ark_home, capsys, monkeypatch):
    d = paths.run_dir("demo", "x")
    info = proc.start(d, "j.worker", ["sleep", "20"], cwd=d, env=dict(os.environ), minutes=5)
    restart()
    with monkeypatch.context() as m:
        m.setattr(proc, "_describe", lambda pid: proc.UNKNOWN)
        code, out = stop_cli(ark_home, capsys)
    assert code == 1 and "no se mandó ninguna señal" in out and "bloqueado" in out
    os.killpg(info["pid"], signal.SIGKILL)
    _mine[-1].wait(10)
    assert proc.reap(d) == ["j.worker"]
    assert proc.consume(d, "j.worker")["reason"] == STOPPED


def test_a_stop_with_an_unreadable_proc_counts_and_warns_it_may_still_run(ark_home, capsys):
    d = paths.run_dir("demo", "x")
    d.mkdir(parents=True)
    (d / "j.worker.proc").write_text("{not json")
    code, out = stop_cli(ark_home, capsys)
    assert code == 1 and "ninguna señal" in out and "bloqueado" in out
    r = proc.consume(d, "j.worker")
    assert r["reason"] == STOPPED and r["survivors"] is True


def test_the_mark_of_an_older_process_does_not_touch_the_next_one(tmp_path):
    (tmp_path / "j.worker.stop").write_text(json.dumps({"pid": 1}))
    proc.start(tmp_path, "j.worker", ["true"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    assert not (tmp_path / "j.worker.stop").exists()
    wait_receipt(tmp_path, "j.worker")
    (tmp_path / "j.worker.stop").write_text(json.dumps({"pid": 1}))             # survived a crash, say
    assert proc.consume(tmp_path, "j.worker")["reason"] == ""


def test_only_a_stop_from_marco_leaves_a_mark(tmp_path):
    proc.start(tmp_path, "j.worker", ["sleep", "20"], cwd=tmp_path, env=dict(os.environ), minutes=5)
    assert proc.stop(tmp_path, "j.worker", "timeout: over its time limit") is True
    assert not (tmp_path / "j.worker.stop").exists()
    assert proc.consume(tmp_path, "j.worker")["reason"].startswith("timeout")


# ---- what a worker planted in the run folder never hangs a read ---------------------------------

class Hung(BaseException):
    """Not an Exception: nothing on the way up may catch it and carry on."""


@pytest.fixture
def must_not_hang():
    def alarm(*_):
        raise Hung()
    before = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, 30)
    yield
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, before)


def test_a_fifo_as_proc_gets_a_failure_and_no_hang(tmp_path, must_not_hang):
    os.mkfifo(tmp_path / "evil.proc")
    assert proc._read(tmp_path / "evil.proc") is None
    assert proc.overdue(tmp_path) == []
    assert proc.reap(tmp_path) == ["evil"]
    assert proc.consume(tmp_path, "evil")["reason"].startswith("unreadable .proc")
    assert proc.active(tmp_path) == []


def test_a_link_to_a_fifo_as_proc_gets_a_failure_and_no_hang(tmp_path, must_not_hang):
    os.mkfifo(tmp_path / "pipe")
    (tmp_path / "link.proc").symlink_to(tmp_path / "pipe")
    assert proc.reap(tmp_path) == ["link"]
    assert proc.consume(tmp_path, "link")["reason"].startswith("unreadable .proc")


def test_a_fifo_as_receipt_is_an_unreadable_receipt(tmp_path, must_not_hang):
    proc.start(tmp_path, "j.worker", ["true"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.worker")
    (tmp_path / "j.worker.receipt.json").unlink()
    os.mkfifo(tmp_path / "j.worker.receipt.json")
    assert proc.consume(tmp_path, "j.worker")["reason"] == "unreadable receipt"


def test_a_fifo_as_the_log_of_a_dead_worker_is_an_empty_log(tmp_path, must_not_hang):
    dead_wrapper_proc(tmp_path, "j.worker")
    (tmp_path / "j.worker.log").unlink(missing_ok=True)
    os.mkfifo(tmp_path / "j.worker.log")
    assert proc.reap(tmp_path) == ["j.worker"]
    r = proc.consume(tmp_path, "j.worker")
    assert r["reason"] == "died without a receipt" and r["result"] == ""


def test_a_fifo_as_the_stop_mark_is_no_mark(tmp_path, must_not_hang):
    proc.start(tmp_path, "j.worker", ["true"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.worker")
    os.mkfifo(tmp_path / "j.worker.stop")
    assert proc.consume(tmp_path, "j.worker")["reason"] == ""


# ---- a stop that crosses the loop's consume: the CLI never claims what did not happen -------------
#
# stop writes its mark, then looks for `.proc`. consume removes `.proc`, then looks for the mark. So at least
# one sees the other: a stop that saw `.proc` always counts, and one that did not cannot know.

UNKNOWABLE = "no se puede saber"


def ended_with_a_natural_receipt(d):
    proc.start(d, "j.worker", ["sh", "-c", "echo 'STATUS: DONE'"], cwd=d, env=dict(os.environ), minutes=5)
    wait_receipt(d, "j.worker")


def run_before_proc_goes(monkeypatch, action):
    """Run `action` inside consume: after its first look at the mark, just before it removes `.proc`."""
    real = Path.unlink

    def unlink(self, *a, **k):
        if self.name == "j.worker.proc":
            monkeypatch.setattr(Path, "unlink", real)
            action()
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", unlink)


def run_around_the_mark(monkeypatch, action, before_write=False):
    """Run `action` inside the stop, just before or just after the write of its mark."""
    real = proc._atomic

    def atomic(path, data):
        if path.name != "j.worker.stop":
            return real(path, data)
        monkeypatch.setattr(proc, "_atomic", real)
        if before_write:
            action()
        real(path, data)
        if not before_write:
            action()
    monkeypatch.setattr(proc, "_atomic", atomic)


def test_a_stop_landing_just_before_consume_removes_the_proc_really_stops_the_job(ark_home, capsys, monkeypatch):
    """The 'detenido' that was false: said after consume's only check, and the job followed the natural receipt."""
    d = paths.run_dir("demo", "x")
    ended_with_a_natural_receipt(d)
    said = {}

    def stop_now():
        said["code"], said["out"] = stop_cli(ark_home, capsys)
    run_before_proc_goes(monkeypatch, stop_now)

    r = proc.consume(d, "j.worker")
    assert said["code"] == 0 and "detenido: j.worker" in said["out"]
    assert (r["reason"], r["exit"], r["cause"]) == (STOPPED, -15, "")
    assert "STATUS: DONE" in r["result"]
    archived = [json.loads(f.read_text()) for f in (d / "done").glob("j.worker-*.json")]
    assert len(archived) == 1 and archived[0]["reason"] == STOPPED          # what is kept says the same
    assert proc.stop_requested(d, "j.worker")                               # until the loop has saved the result


def test_a_stop_that_saw_the_proc_gone_while_consume_missed_its_mark_says_it_cannot_tell(ark_home, capsys, monkeypatch):
    """The job follows its natural receipt here. The CLI may neither say 'detenido' nor blame ps."""
    d = paths.run_dir("demo", "x")
    ended_with_a_natural_receipt(d)
    consumed = {}

    def consume_now():
        consumed["r"] = proc.consume(d, "j.worker")
    run_around_the_mark(monkeypatch, consume_now, before_write=True)

    code, out = stop_cli(ark_home, capsys)
    assert consumed["r"]["reason"] == ""
    assert code == 1 and UNKNOWABLE in out
    assert "detenido" not in out and "ps no respondió" not in out and "nada corriendo" not in out
    assert not (d / "j.worker.stop").exists()                               # it must not speak for a later process


def test_a_stop_that_saw_the_proc_gone_while_consume_took_its_mark_blocks_and_says_it_cannot_tell(
        ark_home, capsys, monkeypatch):
    """Both see each other: it fails closed. The job ends as stopped, and the CLI still claims nothing."""
    d = paths.run_dir("demo", "x")
    ended_with_a_natural_receipt(d)
    consumed = {}

    def consume_now():
        consumed["r"] = proc.consume(d, "j.worker")
    run_around_the_mark(monkeypatch, consume_now)

    code, out = stop_cli(ark_home, capsys)
    assert consumed["r"]["reason"] == STOPPED
    assert code == 1 and UNKNOWABLE in out and "detenido" not in out


def test_a_stop_between_the_two_unlinks_of_consume_cannot_tell(ark_home, capsys, monkeypatch):
    """`.proc` is gone, the receipt not yet: 'nada corriendo' would be a guess, consume may yet see the mark."""
    d = paths.run_dir("demo", "x")
    ended_with_a_natural_receipt(d)
    (d / "j.worker.proc").unlink()
    monkeypatch.setattr(proc, "active", lambda _: ["j.worker"])
    code, out = stop_cli(ark_home, capsys)
    assert code == 1 and UNKNOWABLE in out and "nada corriendo" not in out


def test_a_stop_in_time_says_detenido_even_if_consume_ends_before_the_cli_looks(ark_home, capsys, monkeypatch):
    """The mark was seen with `.proc` in place, so it counts, whatever is gone by the time the CLI words it."""
    d = paths.run_dir("demo", "x")
    ended_with_a_natural_receipt(d)
    real = proc.stop
    consumed = {}

    def stop_then_consume(*a):
        ok = real(*a)
        consumed["r"] = proc.consume(d, "j.worker")
        return ok
    monkeypatch.setattr(proc, "stop", stop_then_consume)
    code, out = stop_cli(ark_home, capsys)
    assert consumed["r"]["reason"] == STOPPED
    assert code == 0 and "detenido: j.worker" in out and "ps no respondió" not in out


def test_consume_second_look_ignores_the_mark_of_an_older_process(tmp_path, monkeypatch):
    proc.start(tmp_path, "j.worker", ["true"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.worker")
    run_before_proc_goes(monkeypatch, lambda: (tmp_path / "j.worker.stop").write_text(json.dumps({"pid": 1})))
    assert proc.consume(tmp_path, "j.worker")["reason"] == ""


def test_a_directory_as_proc_leaks_no_descriptor_through_the_loops_reads(tmp_path):
    (tmp_path / "evil.proc").mkdir()
    before = len(os.listdir("/dev/fd"))
    for _ in range(30):
        assert proc._read(tmp_path / "evil.proc") is None
        assert proc.overdue(tmp_path) == []
    assert len(os.listdir("/dev/fd")) == before
