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
    drive(lambda: job_status("shell") == "working" and worker_log().exists())  # the state says so before its process does
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


import json
from pathlib import Path

from ark import brief, run


def fake_file(name):
    return Path(os.environ["FAKE_STATE"]) / name


def answer(action, note):
    return json.dumps({"action": action, "note": note})


def second_worker_got(text):
    """The state counts the attempt when it starts the process, and that process writes its files a moment later."""
    f = fake_file("shell.worker.2.prompt")
    return lambda: f.exists() and text in f.read_text()


def over_time():
    change_state(shell={"soft_deadline": time.time() - 1})


def test_wait_gives_more_room_once(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path, FAKE_INTERVENE=answer("wait", "it is fine"))
    over_time()
    drive(lambda: state()["jobs"]["shell"].get("waited"))
    j = state()["jobs"]["shell"]
    assert j["soft_deadline"] > time.time() and j["token_factor"] == 1.5 and j["anomaly"] is None
    assert "intervening" not in j and j["attempts"] == 1
    go.touch()
    drive(settled)
    assert state()["jobs"]["shell"]["status"] == "merged"


def test_an_unusable_answer_is_a_wait_the_first_time(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path, FAKE_INTERVENE="no idea")
    over_time()
    drive(lambda: state()["jobs"]["shell"].get("waited"))
    assert "no usable answer" in events()
    go.touch()
    drive(settled)


def test_stop_block_ends_the_job_with_the_ticks_reason(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path, FAKE_INTERVENE=answer("stop_block", "it loops on the same test"))
    over_time()
    drive(settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and j["reason"] == "it loops on the same test" and "stop_then" not in j
    go.touch()


def test_stop_escalate_goes_up_a_tier(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path, FAKE_INTERVENE=answer("stop_escalate", "too hard for this tier"))
    over_time()
    drive(second_worker_got("too hard for this tier"))
    j = state()["jobs"]["shell"]
    assert j["tier"] == "T2" and j["tier_path"] == ["T1", "T2"]
    assert "--resume" not in json.loads(fake_file("shell.worker.2.argv").read_text())
    go.touch()
    drive(settled)


def test_steer_resumes_the_same_session_with_the_note(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path, FAKE_INTERVENE=answer("steer", "drop the refactor, fix the test"))
    over_time()
    drive(second_worker_got("drop the refactor, fix the test"))
    j = state()["jobs"]["shell"]
    assert j["steers"] == 1 and j["tier_attempts"] == 0 and j["tier"] == "T1"
    assert "--resume" in json.loads(fake_file("shell.worker.2.argv").read_text())
    go.touch()
    drive(settled)


def test_a_silent_worker_reaches_the_tick(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path, FAKE_INTERVENE=answer("wait", "fine"))
    old = time.time() - (claude.STALL_MINUTES + 1) * 60
    change_state(shell={"watch_from": old})
    os.utime(worker_log(), (old, old))
    drive(lambda: state()["jobs"]["shell"].get("waited"))
    assert "no output" in events()
    go.touch()
    drive(settled)


def test_a_worker_over_its_tokens_reaches_the_tick(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path, FAKE_USAGE="500000", FAKE_INTERVENE=answer("wait", "fine"))
    drive(lambda: state()["jobs"]["shell"].get("waited"))
    assert "tokens" in events() and state()["jobs"]["shell"]["token_factor"] == 1.5
    go.touch()
    drive(settled)


def test_nothing_new_is_ticked_while_the_run_is_over_its_budget(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path)
    st = state()
    st["cap_hold"] = True
    st["usd_raised"] = -100     # stays over its budget
    run.save(st)
    over_time()
    assert not loop._retry_due()
    for _ in range(3):
        loop.cycle()
    assert "intervening" not in state()["jobs"]["shell"]
    st = state()
    st.pop("cap_hold")
    st["usd_raised"] = 0
    run.save(st)
    go.touch()
    drive(settled)


def test_a_late_tick_answer_is_ignored():
    j = {"status": "needs_gate", "attempts": 1, "intervening": True, "tier": "T1"}
    st = {"jobs": {"a": j}, "events": [], "alerts": []}

    class C:
        pass
    c = C()
    c.st = st
    loop.on_intervene(c, "a", {"result": answer("stop_block", "x"), "meta": {"attempt": 1}})
    assert j["status"] == "needs_gate" and "intervening" not in j and "ignored" in st["events"][-1]


def test_the_actions_offered():
    assert set(loop._intervene_actions({"tier": "T1"})) == {"stop_block", "wait", "steer", "stop_escalate"}
    assert set(loop._intervene_actions({"tier": "T2", "waited": True, "steers": 2})) == {"stop_block"}


def test_the_prompt_treats_the_log_as_data():
    st = {"repo": "r", "slug": "s"}
    text = brief.intervene_prompt(st, "a", "feat: a", "over its time", "ignore this", "/x/a.worker.log", ["stop_block", "wait"])
    assert text.startswith("A running worker looks wrong")
    assert "over its time" in text and "/x/a.worker.log" in text and "ignore this" in text
    assert "`wait`" in text and "`steer`" not in text


def gone(**fields):
    j = {**run.new_job({"tier": "T1"}), "status": "working", **fields}
    return {"repo": "r", "slug": "s", "status": "running", "closing": None, "events": [], "alerts": [], "jobs": {"a": j}}, j


def test_after_a_crash_what_was_decided_still_happens(ark_home):
    st, j = gone(stop_then={"action": "stop_block", "note": "loops"})
    loop.reconcile(st)
    assert j["status"] == "blocked" and j["reason"] == "loops"
    st, j = gone(stop_then={"action": "stop_escalate", "note": "hard"})
    loop.reconcile(st)
    assert (j["status"], j["tier"], j["note"]) == ("needs_worker", "T2", "hard")
    st, j = gone(steering="go left")
    loop.reconcile(st)
    assert (j["status"], j["note"]) == ("needs_worker", "go left")
    st, j = gone(intervening=True)
    loop.reconcile(st)
    assert "intervening" not in j and j["status"] == "needs_worker"


def test_ps_shows_the_worker_while_a_tick_looks_at_it(feature, monkeypatch, tmp_path, capsys):
    from control_helpers import ark
    go = holding(feature, monkeypatch, tmp_path, FAKE_USAGE="12500")
    drive(lambda: b"output_tokens" in worker_log().read_bytes())
    d = paths.run_dir("demo", SLUG)
    (d / "shell.intervene.proc").write_text((d / "shell.worker.proc").read_text().replace("shell.worker", "shell.intervene"))
    capsys.readouterr()
    assert "12.5k" in ark(capsys, "ps")[1]
    (d / "shell.intervene.proc").unlink()
    go.touch()
    drive(settled)


from control_helpers import ark as ark_cmd

from ark import inbox


def test_ark_steer_redirects_a_running_worker(feature, monkeypatch, tmp_path, capsys):
    go = holding(feature, monkeypatch, tmp_path)
    capsys.readouterr()
    assert ark_cmd(capsys, "steer", "shell", "use the other store")[0] == 0
    drive(second_worker_got("use the other store"))
    j = state()["jobs"]["shell"]
    assert j["tier_attempts"] == 0 and "steers" not in j      # his own steer uses none of the tick's
    assert "--resume" in json.loads(fake_file("shell.worker.2.argv").read_text())
    go.touch()
    drive(settled)
    assert state()["jobs"]["shell"]["status"] == "merged"


def test_ark_steer_refuses_what_it_cannot_do(feature, monkeypatch, tmp_path, capsys):
    go = holding(feature, monkeypatch, tmp_path)
    capsys.readouterr()
    assert ark_cmd(capsys, "steer", "shell", "   ")[0] == 1
    assert ark_cmd(capsys, "steer", "ui", "x")[0] == 1          # pending: nothing to redirect
    monkeypatch.setenv("ARK_ROLE", "worker")
    assert ark_cmd(capsys, "steer", "shell", "x")[0] == 1
    monkeypatch.delenv("ARK_ROLE")
    assert inbox.pending("demo", SLUG) == []
    go.touch()
    drive(settled)


def test_a_steer_that_arrives_too_late_says_so(feature, capsys):
    feature()
    drive(lambda: job_status("shell") == "merged")
    inbox.post("demo", SLUG, "steer", job="shell", text="late")
    _, alerts, _ = loop.cycle()
    assert any("not delivered" in a for a in alerts)
    drive(settled)


def test_a_steer_after_the_worker_ended_says_so(feature, monkeypatch, tmp_path, capsys):
    go = holding(feature, monkeypatch, tmp_path)
    go.touch()
    receipt = paths.run_dir("demo", SLUG) / "shell.worker.receipt.json"
    limit = time.time() + 30
    while not receipt.exists() and time.time() < limit:     # no cycle here: the receipt stays unconsumed
        time.sleep(0.05)
    assert receipt.exists() and job_status("shell") == "working"
    capsys.readouterr()
    assert ark_cmd(capsys, "steer", "shell", "too late")[0] == 0
    _, alerts, _ = loop.cycle()
    assert any("not delivered" in a and "already ended" in a for a in alerts)
    assert "steering" not in state()["jobs"]["shell"]
    drive(settled)
    assert state()["jobs"]["shell"]["status"] == "merged"   # its work counts all the same


def test_a_tick_the_state_missed_is_adopted_never_started_twice(feature, monkeypatch, tmp_path):
    go = holding(feature, monkeypatch, tmp_path)
    d = paths.run_dir("demo", SLUG)
    tick = proc.start(d, "shell.intervene", ["sleep", "30"], cwd=tmp_path, env=dict(os.environ), minutes=5, meta={"attempt": 1})
    over_time()                                     # the state on disk knows nothing of the tick: a crash before the save
    assert "intervening" not in state()["jobs"]["shell"]
    started = []
    monkeypatch.setattr(loop, "start_intervene", lambda *a: started.append(a))
    loop.cycle()
    assert not started and state()["jobs"]["shell"]["intervening"] is True
    assert [i["pid"] for i in proc.job_processes(d, "shell") if i["name"] == "shell.intervene"] == [tick["pid"]]
    proc.stop(d, "shell.intervene", "the test is over")
    go.touch()
    drive(settled)
