"""A worker whose turn an API failure cut while its process stays alive is stopped and resumed in its session."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from control_helpers import SLUG, change_state, drive, settled, state, workspace
from helpers import no_load_brake

from ark import brief, claude, loop, paths, proc, run, usage

TAIL = Path(__file__).parent / "fixtures" / "api_error_tail.jsonl"
ERROR = "API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)"


@pytest.fixture(autouse=True)
def _no_load_brake(ark_home):
    no_load_brake(ark_home)


def fake_file(name):
    return Path(os.environ["FAKE_STATE"]) / name


def events():
    return "\n".join(state()["events"])


def worker_log():
    return paths.run_dir("demo", SLUG) / "shell.worker.log"


def assistant(text, model="claude-opus-5-5", mid="m-after"):
    return json.dumps({"type": "assistant", "message": {"id": mid, "model": model,
                                                        "content": [{"type": "text", "text": text}]}}) + "\n"


# ---- T1: the log scan ------------------------------------------------------------------------

def test_the_scan_tells_an_api_error_at_the_end(tmp_path):
    log = tmp_path / "w.log"
    log.write_bytes(TAIL.read_bytes())
    s = usage.scan(log)
    assert s["api_error"] == ERROR
    lines = TAIL.read_bytes().split(b"\n")
    at = next(i for i, raw in enumerate(lines) if b"<synthetic>" in raw)
    assert s["api_error_at"] == sum(len(raw) + 1 for raw in lines[:at])
    assert s["tokens"] == 0 and s["last"] == ERROR   # the old keys are as before


def test_a_normal_assistant_line_after_it_clears_it(tmp_path):
    log = tmp_path / "w.log"
    log.write_bytes(TAIL.read_bytes())
    assert usage.scan(log)["api_error"]
    with open(log, "a") as h:
        h.write(json.dumps({"type": "system", "subtype": "api_retry", "attempt": 1}) + "\n")
    assert usage.scan(log)["api_error"] == ERROR    # system lines leave it as it is
    with open(log, "a") as h:
        h.write(assistant("back to work"))
    s = usage.scan(log)
    assert s["api_error"] is None and s["api_error_at"] is None


def test_retries_alone_are_no_api_error(tmp_path):
    log = tmp_path / "w.log"
    log.write_bytes(b"".join(raw + b"\n" for raw in TAIL.read_bytes().split(b"\n") if b"api_retry" in raw))
    s = usage.scan(log)
    assert s["api_error"] is None and s["api_error_at"] is None


def test_a_real_model_saying_api_error_is_not_one(tmp_path):
    log = tmp_path / "w.log"
    log.write_text(assistant("API Error: is what the user saw"))
    assert usage.scan(log)["api_error"] is None


def test_a_missing_log_has_the_keys_too(tmp_path):
    s = usage.scan(tmp_path / "none.log")
    assert s["api_error"] is None and s["api_error_at"] is None


# ---- T2: the limits --------------------------------------------------------------------------

def test_the_limits_have_defaults():
    limits = paths.DEFAULTS["limits"]
    assert limits["stall_minutes"] == claude.STALL_MINUTES == 20
    assert limits["api_quiet_minutes"] == 3 and limits["auto_resumes"] == 3


def test_the_stall_follows_the_workspace(ark_home, tmp_path):
    workspace(ark_home, tmp_path, stall_minutes=5)
    limits = paths.load_workspace()["limits"]
    log = tmp_path / "w.log"
    log.write_text("")
    now = time.time()
    old = now - 6 * 60
    os.utime(log, (old, old))
    j = {"tier": "T1", "soft_deadline": now + 100, "watch_from": old, "token_factor": 1}
    assert "no output for over 5 minutes" in loop._anomaly(j, log, now, limits)
    assert loop._anomaly(j, log, now, {**limits, "stall_minutes": 20}) is None


def test_the_resume_message():
    assert brief.RESUME["api"] == ("The API failed and your turn was cut. Nothing is lost: go on with the job "
                                   "from where you were. Check `git status` and your report first.")


# ---- T3: watch() stops a hung worker ---------------------------------------------------------

def ctx(tmp_path, **fields):
    now = time.time()
    j = {**run.new_job({"tier": "T1"}), "status": "working", "attempts": 1, "soft_deadline": now + 3600,
         "watch_from": now - 3600, "token_factor": 1, **fields}
    st = {"repo": "demo", "slug": SLUG, "events": [], "alerts": [], "jobs": {"a": j}}
    d = tmp_path / "run"
    d.mkdir(exist_ok=True)
    log = d / "a.worker.log"
    log.write_bytes(TAIL.read_bytes())
    old = now - 4 * 60
    os.utime(log, (old, old))
    return SimpleNamespace(st=st, dir=d), j, log


@pytest.fixture
def stops(monkeypatch):
    out = []
    monkeypatch.setattr(loop.proc, "stop", lambda d, name, reason: out.append((name, reason)) or True)
    monkeypatch.setattr(loop.proc, "finished", lambda d: [])
    monkeypatch.setattr(loop.proc, "stop_requested", lambda d, name: False)
    return out


@pytest.fixture
def ticks(monkeypatch):
    out = []
    monkeypatch.setattr(loop, "start_intervene", lambda c, job, why: out.append(why))
    return out


def test_a_quiet_api_error_stops_the_worker(tmp_path, stops, ticks):
    c, j, _ = ctx(tmp_path)
    loop.watch(c)
    assert stops == [("a.worker", loop.API_HUNG)] and not ticks
    assert j["api_hung"] == ERROR and j["auto_resumes"] == 1
    assert "api_hung" in loop.HANDLING and loop.API_HUNG in loop.OURS
    loop.watch(c)   # it is being handled now: nothing more
    assert len(stops) == 1


def test_a_new_line_inside_the_quiet_window_does_nothing(tmp_path, stops, ticks):
    c, j, log = ctx(tmp_path)
    os.utime(log, None)
    loop.watch(c)
    assert not stops and not ticks and "api_hung" not in j


def test_a_job_being_handled_is_never_resumed(tmp_path, stops, ticks):
    for key in loop.HANDLING:
        c, j, _ = ctx(tmp_path, **{key: True})
        loop.watch(c)
        assert not stops and j.get("auto_resumes", 0) == 0


def test_a_job_marco_stopped_is_never_resumed(tmp_path, stops, ticks, monkeypatch):
    monkeypatch.setattr(loop.proc, "stop_requested", lambda d, name: True)
    c, j, _ = ctx(tmp_path)
    loop.watch(c)
    assert not stops and "api_hung" not in j


def test_the_fourth_hang_takes_the_intervene_path(tmp_path, stops, ticks):
    c, j, log = ctx(tmp_path, auto_resumes=3)
    loop.watch(c)
    assert not stops and "api_hung" not in j and not ticks   # quiet, but not yet past stall_minutes
    old = time.time() - (claude.STALL_MINUTES + 1) * 60
    os.utime(log, (old, old))
    loop.watch(c)
    assert not stops and ticks and "no output" in ticks[0]


def test_the_quiet_window_follows_the_workspace(ark_home, tmp_path, stops, ticks):
    workspace(ark_home, tmp_path, api_quiet_minutes=10)
    c, j, _ = ctx(tmp_path)
    loop.watch(c)
    assert not stops


# ---- T3: the resume, end to end with the fake worker -----------------------------------------

def holding(feature, monkeypatch, tmp_path, **env):
    go = tmp_path / "go"
    monkeypatch.setenv("FAKE_HOLD", str(go))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    feature()
    drive(lambda: state()["jobs"]["shell"]["status"] == "working" and fake_file("shell.worker.1.prompt").exists())
    return go


def hang():
    """The worker's log ends in the API error, in its own session, and has been quiet for a while."""
    sid = proc.job_processes(paths.run_dir("demo", SLUG), "shell")[0]["meta"]["session"]
    with open(worker_log(), "a") as h:
        for raw in TAIL.read_text().splitlines():
            d = json.loads(raw)
            d["session_id"] = sid
            h.write(json.dumps(d) + "\n")
    old = time.time() - 4 * 60
    os.utime(worker_log(), (old, old))
    change_state(shell={"watch_from": old})
    return sid


def test_a_hung_worker_is_resumed_in_its_session_with_nothing_charged(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path)
    before = dict(state()["jobs"]["shell"])
    sid = hang()
    drive(lambda: fake_file("shell.worker.2.prompt").exists())
    prompt = fake_file("shell.worker.2.prompt").read_text()
    assert prompt.startswith(brief.RESUME["api"]) and ERROR in prompt
    argv = json.loads(fake_file("shell.worker.2.argv").read_text())
    assert argv[argv.index("--resume") + 1] == sid
    j = state()["jobs"]["shell"]
    assert j["attempts"] == before["attempts"]   # the resumed worker is the same attempt
    for k in ("tier_attempts", "steers", "review_rounds"):
        assert j.get(k, 0) == before.get(k, 0)
    assert j["auto_resumes"] == 1 and "api_hung" not in j
    said = [e for e in state()["events"] if "resumed after an API failure" in e]
    assert len(said) == 1 and said[0].endswith(f" · shell: resumed after an API failure (1/3): {ERROR}")
    go.touch()
    drive(settled)
    assert state()["jobs"]["shell"]["status"] == "merged"


def test_a_new_attempt_resets_the_count():
    j = {**run.new_job({"tier": "T1"}), "auto_resumes": 3, "tier_path": ["T1"]}
    loop._escalate(j)
    assert "auto_resumes" not in j
    st = {"repo": "demo", "slug": SLUG, "events": [], "alerts": [], "jobs": {"a": {**j, "auto_resumes": 2}}}
    loop._go_on(st, "a", {"trigger": "failure", "situation": "red"}, "try again")
    assert "auto_resumes" not in st["jobs"]["a"]


def test_fail_resets_the_count(monkeypatch):
    j = {**run.new_job({"tier": "T1"}), "auto_resumes": 2, "status": "working"}
    st = {"repo": "demo", "slug": SLUG, "events": [], "alerts": [], "jobs": {"a": j}}
    monkeypatch.setattr(loop.progress, "stalled", lambda j: False)
    loop.fail(SimpleNamespace(st=st), "a", "red")
    assert "auto_resumes" not in j and j["tier_attempts"] == 1


# ---- T4: an intervene tick with no answer ----------------------------------------------------

def tick_receipt(**fields):
    now = time.time()
    return {"exit": 1, "reason": "", "start": now - 5, "end": now, "is_error": None, "spoke": False, "result": "",
            "usd": None, "session": None, "meta": {"attempt": 1}, **fields}


def test_a_tick_that_died_at_once_resumes_the_worker(tmp_path, stops):
    c, j, _ = ctx(tmp_path, anomaly="no output for over 20 minutes")
    loop.on_intervene(c, "a", tick_receipt())
    assert stops == [("a.worker", loop.API_HUNG)]
    assert j["api_hung"] == "no output for over 20 minutes" and j["auto_resumes"] == 1 and not j.get("waited")


def test_a_tick_that_timed_out_silent_resumes_the_worker(tmp_path, stops):
    c, j, _ = ctx(tmp_path, anomaly="over its time for tier T1")
    now = time.time()
    loop.on_intervene(c, "a", tick_receipt(exit=-15, reason="timeout: over its time limit", start=now - 900, end=now))
    assert stops == [("a.worker", loop.API_HUNG)] and j["api_hung"] == "over its time for tier T1"


def test_a_tick_that_spoke_without_an_action_keeps_the_fallback(tmp_path, stops):
    c, j, _ = ctx(tmp_path, anomaly="over its time for tier T1")
    loop.on_intervene(c, "a", tick_receipt(exit=0, spoke=True, is_error=False, result="no idea", usd=0.2))
    assert not stops and j["waited"] and "api_hung" not in j


def test_with_the_resumes_spent_the_fallback_stays(tmp_path, stops):
    c, j, _ = ctx(tmp_path, anomaly="over its time for tier T1", auto_resumes=3)
    loop.on_intervene(c, "a", tick_receipt())
    assert not stops and j["waited"] and "api_hung" not in j


def test_a_dead_tick_resumes_the_worker_end_to_end(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_DOWN", "1")   # only the intervene tick sees it: the worker already runs
    change_state(shell={"soft_deadline": time.time() - 1})
    drive(lambda: state()["jobs"]["shell"].get("intervening"))
    monkeypatch.delenv("FAKE_DOWN")
    drive(lambda: fake_file("shell.worker.2.prompt").exists())
    assert fake_file("shell.worker.2.prompt").read_text().startswith(brief.RESUME["api"])
    j = state()["jobs"]["shell"]
    assert j["attempts"] == 1 and not j.get("waited")
    assert "shell: resumed after an API failure (1/3): over its time for tier T1" in events()
    go.touch()
    drive(settled)
