import fcntl
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
from helpers import approve, plan_text

import conftest
from ark import paths, proc, run
from test_merge import check, is_gone, ready, scratch_left  # noqa: F401  (fixtures of a real check)

CODE = Path(__file__).resolve().parents[1]
SLUG = "notes-edit"


def env_for(ark_home, **extra):
    """What every program under bin/ runs with. Real processes see none of this suite's patches, only the
    environment, so it names the fakes and it refuses a home outside the sandbox. `claude` and `gh` by
    their real names land on the suite's tripwire shims, first on the inherited PATH."""
    fake = Path(ark_home).parent / "fake"
    fake.mkdir(exist_ok=True)
    env = {"ARK_CLAUDE": str(CODE / "tests" / "fake_claude.py"), "ARK_FORGE": "local", "FAKE_STATE": str(fake),
           **os.environ, "ARK_HOME": str(ark_home)}
    env.pop("ARK_ROLE", None)
    env.update(extra)
    for p in (env["ARK_HOME"], env["ARK_WORKTREES"], Path(env["HOME"]) / ".claude" / "arkimedes"):
        assert not Path(p).resolve().is_relative_to(conftest._REAL_CLAUDE_DIR), f"a test reached the real home: {p}"
    assert env["PATH"].startswith(str(conftest._SANDBOX / "bin")), "a real gh or claude could run"
    return env


def test_bin_ark_runs_the_cli(make_feature, ark_home):
    make_feature()
    r = subprocess.run([str(CODE / "bin/ark"), "status"], capture_output=True, text=True, env=env_for(ark_home))
    assert r.returncode == 0, r.stderr
    assert "notes-edit" in r.stdout


def test_bin_ark_validate(make_feature, ark_home):
    p = make_feature()
    r = subprocess.run([str(CODE / "bin/ark-validate"), str(p)], capture_output=True, text=True,
                       env=env_for(ark_home))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("OK")


def test_ark_loop_once_idle(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                       env=env_for(ark_home, ARK_FORGE="local"))
    assert r.returncode == 0, r.stderr
    assert "idle" in r.stdout


def test_ark_loop_refuses_an_api_key(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                       env=env_for(ark_home, ANTHROPIC_API_KEY="sk-x"))
    assert r.returncode == 2 and "ANTHROPIC_API_KEY" in r.stderr


def test_ark_loop_runs_once_at_a_time(ark_home):
    (ark_home / "runs").mkdir()
    with open(ark_home / "runs" / ".loop.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                           env=env_for(ark_home))
    assert r.returncode == 1 and "is already running" in r.stderr


def test_ark_dispatch_only_stops(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-dispatch"), "run", "x"], capture_output=True, text=True,
                       env=env_for(ark_home))
    assert r.returncode == 2 and "uso" in r.stderr
    r = subprocess.run([str(CODE / "bin/ark-dispatch"), "stop", "demo/x", "shell"], capture_output=True,
                       text=True, env=env_for(ark_home))
    assert r.returncode == 1 and "nothing running" in r.stdout


# ---- the suite's guard, for programs that see none of its patches ----------------------------------

@pytest.mark.parametrize("var", ["ARK_HOME", "ARK_WORKTREES"])
def test_env_for_refuses_the_real_home(ark_home, var):
    with pytest.raises(AssertionError, match="real home"):
        env_for(ark_home, **{var: str(conftest._REAL_CLAUDE_DIR / "arkimedes")})


def test_env_for_refuses_the_real_user_home(ark_home):
    with pytest.raises(AssertionError, match="real home"):
        env_for(ark_home, HOME=str(conftest._REAL_CLAUDE_DIR.parent))


def test_env_for_refuses_a_path_without_the_tripwire_shims(ark_home):
    with pytest.raises(AssertionError, match="real gh or claude"):
        env_for(ark_home, PATH="/usr/bin:/bin")


def test_env_for_names_the_fakes(ark_home):
    env = env_for(ark_home)
    assert env["ARK_FORGE"] == "local" and env["ARK_CLAUDE"] == str(CODE / "tests" / "fake_claude.py")
    assert Path(env["FAKE_STATE"]).is_dir()


def test_a_wrapper_that_loses_its_home_stays_in_the_sandbox(ark_home):
    env = env_for(ark_home)
    del env["ARK_HOME"]
    try:
        r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        assert (conftest._SANDBOX / "home" / ".claude" / "arkimedes" / "runs" / ".loop.lock").exists()
    finally:
        shutil.rmtree(conftest._SANDBOX / "home" / ".claude", ignore_errors=True)


# ---- how Marco starts them: through a symlink, from anywhere ---------------------------------------

def decoys(where):
    """A directory where `python -m` would import the wrong code if it counted the current directory."""
    mark = where / "decoy-ran"
    code = f"open({str(mark)!r}, 'w').close()\nraise SystemExit(7)\n"
    (where / "json.py").write_text(code)
    (where / "ark").mkdir()
    (where / "ark" / "__init__.py").write_text(code)
    return mark


@pytest.mark.parametrize("name,args,code", [("ark-loop", ["--once"], 0), ("ark-merge", [], 2),
                                            ("ark-dispatch", ["stop", "demo/x", "shell"], 1),
                                            ("ark-integrate", [], 2)])
def test_a_wrapper_works_through_a_symlink_from_any_directory(ark_home, tmp_path, name, args, code):
    elsewhere = tmp_path / "some where"
    elsewhere.mkdir()
    mark = decoys(elsewhere)
    links = tmp_path / "local bin"
    links.mkdir()
    (links / "hop").symlink_to(CODE / "bin" / name)
    (links / name).symlink_to("hop")                                            # relative, and a link to a link
    env = env_for(ark_home)
    env["PATH"] = f"{env['PATH']}:{links}"
    r = subprocess.run([name, *args], cwd=elsewhere, capture_output=True, text=True, env=env)
    assert r.returncode == code, r.stdout + r.stderr
    assert not mark.exists()


def test_a_whole_run_through_the_symlink_from_any_directory(runner, make_feature, ark_home, tmp_path):
    """Every program the loop starts runs for real, and none imports from where Marco stood, nor from the
    target repo: there, a file named like a module of the standard library is normal."""
    repo = runner()
    repo_mark = tmp_path / "repo-decoy-ran"
    (repo / "json.py").write_text(f"open({str(repo_mark)!r}, 'w').close()\nraise SystemExit(7)\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "a module named like the stdlib"], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "master"], cwd=repo, check=True)
    approve(make_feature(plan_text(), create_repo=False))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    mark = decoys(elsewhere)
    link = tmp_path / "ark-loop"
    link.symlink_to(CODE / "bin" / "ark-loop")
    env = env_for(ark_home)
    report = paths.runs_dir() / "demo" / f"{SLUG}.md"
    end = time.monotonic() + 90
    while True:
        r = subprocess.run([str(link), "--once"], cwd=elsewhere, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        st = run.load("demo", SLUG)
        if st and st["status"] in ("done", "blocked") and not proc.active(paths.run_dir("demo", SLUG)):
            break
        assert time.monotonic() < end, report.read_text()
        time.sleep(0.1)
    assert st["status"] == "done", report.read_text()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"]
    assert not mark.exists() and not repo_mark.exists()


# ---- staying up ------------------------------------------------------------------------------------

def wait_for(what, why, seconds=60):
    end = time.monotonic() + seconds
    while not what():
        assert time.monotonic() < end, why()
        time.sleep(0.05)


def test_the_loop_wakes_on_an_approval_and_ctrl_c_ends_it(runner, make_feature, ark_home, tmp_path):
    runner()
    f = ark_home / "workspace.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "tick_minutes": 60, "poll_seconds": 0.2}))
    p = make_feature(plan_text(), create_repo=False)
    out, err = tmp_path / "out", tmp_path / "err"
    with open(out, "w") as o, open(err, "w") as e:
        looping = subprocess.Popen([str(CODE / "bin/ark-loop")], stdout=o, stderr=e, env=env_for(ark_home),
                                   start_new_session=True,
                                   preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    try:
        wait_for(lambda: "idle" in out.read_text(), err.read_text)
        second = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                                env=env_for(ark_home))
        assert second.returncode == 1 and "is already running" in second.stderr
        approve(p)                                                              # the tick is an hour away

        def over():
            st = run.load("demo", SLUG)
            return bool(st) and st["status"] == "done"
        wait_for(over, lambda: out.read_text() + err.read_text())
        os.killpg(looping.pid, signal.SIGINT)                                   # what a terminal sends
        assert looping.wait(20) == 130
        assert "Traceback" not in err.read_text(), err.read_text()
    finally:
        if looping.poll() is None:
            os.killpg(looping.pid, signal.SIGKILL)
            looping.wait()


@pytest.mark.parametrize("at_launch", [signal.SIG_DFL, signal.SIG_IGN], ids=["default", "ignored"])
def test_ctrl_c_stops_the_loop_however_it_was_launched(runner, ark_home, tmp_path, at_launch):
    """A shell without job control starts `ark-loop &` with SIGINT ignored, and Python then installs no
    handler of its own. What the suite itself inherited must not decide this test either."""
    runner()
    f = ark_home / "workspace.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "tick_minutes": 60, "poll_seconds": 0.2}))
    out, err = tmp_path / "out", tmp_path / "err"
    with open(out, "w") as o, open(err, "w") as e:
        looping = subprocess.Popen([str(CODE / "bin/ark-loop")], stdout=o, stderr=e, env=env_for(ark_home),
                                   start_new_session=True, preexec_fn=lambda: signal.signal(signal.SIGINT, at_launch))
    try:
        wait_for(lambda: "idle" in out.read_text(), err.read_text)
        os.killpg(looping.pid, signal.SIGINT)
        assert looping.wait(20) == 130
        assert "Traceback" not in err.read_text(), err.read_text()
    finally:
        if looping.poll() is None:
            os.killpg(looping.pid, signal.SIGKILL)
            looping.wait()


@pytest.mark.parametrize("var", ["ARK_HOME", "ARK_WORKTREES"])
def test_the_loop_refuses_a_relative_home_instead_of_making_one_inside_the_code(ark_home, var):
    name = "rel-home-probe-should-not-exist"
    env = env_for(ark_home)
    env[var] = name
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True, env=env, cwd=ark_home)
    assert r.returncode == 2 and var in r.stderr
    assert not (CODE / name).exists()


def test_a_fifo_in_a_run_folder_does_not_hang_a_loop_start(ark_home):
    d = ark_home / "runs" / "demo" / SLUG
    d.mkdir(parents=True)
    os.mkfifo(d / "evil.proc")
    (ark_home / "approvals" / "demo").mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                       env=env_for(ark_home), timeout=60)
    assert r.returncode == 0, r.stderr


def test_a_worker_outlives_the_loop_without_its_lock_and_ark_dispatch_stops_it(runner, make_feature, ark_home):
    runner()
    approve(make_feature(plan_text(), create_repo=False))
    env = env_for(ark_home, FAKE_SLEEP="30")
    d = paths.run_dir("demo", SLUG)
    info = None
    try:
        for _ in range(10):  # the start check goes green in a tick or two, then the worker starts
            r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True, env=env)
            assert r.returncode == 0, r.stderr
            info = proc._read(d / "shell.worker.proc")
            if info:
                break
            time.sleep(0.5)
        assert info and not is_gone(info["pid"], seconds=0.2)
        with open(ark_home / "runs" / ".loop.lock", "a") as lock:               # no child kept it
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = subprocess.run([str(CODE / "bin/ark-dispatch"), "stop", f"demo/{SLUG}", "shell"], capture_output=True,
                           text=True, env=env)
        assert r.returncode == 0 and "stopped" in r.stdout, r.stdout + r.stderr
        assert is_gone(info["pid"])
        r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        assert run.load("demo", SLUG)["jobs"]["shell"]["status"] == "blocked"
    finally:
        if info:
            try:
                os.killpg(info["pid"], signal.SIGKILL)
            except OSError:
                pass


# ---- what it refuses -------------------------------------------------------------------------------

def test_ark_loop_refuses_to_run_inside_a_runner_process(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                       env=env_for(ark_home, ARK_ROLE="worker"))
    assert r.returncode == 2 and "runner" in r.stderr


@pytest.mark.parametrize("arg", ["--help", "--oncee", "once"])
def test_ark_loop_does_not_start_on_an_argument_it_does_not_know(ark_home, arg):
    r = subprocess.run([str(CODE / "bin/ark-loop"), arg], capture_output=True, text=True, env=env_for(ark_home),
                       timeout=30)
    assert r.returncode == 2 and "usage" in r.stderr
    assert not (ark_home / "runs").exists()


def test_a_link_at_the_lock_empties_nothing(ark_home, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    (ark_home / "runs").mkdir()
    (ark_home / "runs" / ".loop.lock").symlink_to(victim)
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True, env=env_for(ark_home))
    assert r.returncode == 1 and "Traceback" not in r.stderr and "lock" in r.stderr, r.stderr
    assert victim.read_text() == "keep me"


def test_an_alert_reaches_a_terminal_that_cannot_show_it(runner, make_feature, ark_home):
    runner()
    approve(make_feature(plan_text(), create_repo=False))
    run.state_file("demo", SLUG).parent.mkdir(parents=True)
    run.state_file("demo", SLUG).write_text("{damaged")
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                       env=env_for(ark_home, PYTHONIOENCODING="ascii"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "skipped until its state is repaired" in r.stdout


# ---- ark-integrate is integrate.main ---------------------------------------------------------------

def test_ark_integrate_prints_its_usage(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-integrate")], capture_output=True, text=True, env=env_for(ark_home))
    assert r.returncode == 2 and "uso: ark-integrate" in r.stderr


def test_ark_integrate_answers_in_one_json_line(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-integrate"), "demo/x", "shell", "abc"], capture_output=True, text=True,
                       env=env_for(ark_home))
    assert r.returncode == 1 and json.loads(r.stdout)["ok"] is False


def test_stopping_the_wrapper_goes_through_integrate_main(check):
    """Only main's handler answers a stop by removing the worktree: integrate() alone would not."""
    c = check()
    c.argv = [str(CODE / "bin" / "ark-integrate"), *c.argv[3:]]
    c.in_a_terminal()
    assert scratch_left()
    c.popen.send_signal(signal.SIGTERM)
    assert c.popen.wait(30) == 128 + signal.SIGTERM
    assert c.all_gone() and not scratch_left()
