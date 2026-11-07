from control_helpers import SLUG, ark, drive, job_status, settled, state

from ark import inbox, loop, paths, proc, run


def held(feature, monkeypatch, tmp_path):
    go = tmp_path / "go"
    monkeypatch.setenv("FAKE_HOLD", str(go))
    _, plan = feature()
    drive(lambda: job_status("shell") == "working")
    original = plan.read_text()
    plan.write_text(original + "\n")
    drive(lambda: state()["status"] == "held")
    return go, plan, original


def test_a_held_run_has_an_open_question_and_no_second_alert(feature, monkeypatch, tmp_path):
    go, plan, original = held(feature, monkeypatch, tmp_path)
    q = state()["questions"][0]
    assert q["kind"] == "plan_changed" and "stop" in q["text"]
    plan.write_text(original)
    drive(lambda: state()["status"] == "running")
    assert state()["questions"] == []
    go.touch()
    drive(settled)
    assert state()["jobs"]["shell"]["status"] == "merged"


def test_answering_stop_ends_the_run(feature, monkeypatch, tmp_path, capsys):
    go, plan, _ = held(feature, monkeypatch, tmp_path)
    capsys.readouterr()
    assert ark(capsys, "answer", "--run", f"demo/{SLUG}", "q1", "stop")[0] == 0
    drive(lambda: state()["status"] == "blocked")
    st = state()
    assert st["closing"] == "done" and st["final_pr"] is None and st["reason"] == loop.ENDED
    assert {j["status"] for j in st["jobs"].values()} == {"blocked"}
    assert st["jobs"]["shell"]["reason"] == loop.ENDED
    assert st["questions"] == [] and "ending" not in st
    assert proc.active(paths.run_dir("demo", SLUG)) == []
    go.touch()


def test_a_merged_job_stays_merged_when_the_run_ends(feature, monkeypatch, tmp_path):
    feature()
    drive(lambda: job_status("shell") == "merged")
    st = state()
    loop._begin_end(st, loop.ENDED)
    run.save(st)
    drive(lambda: state()["status"] == "blocked")
    st = state()
    assert st["jobs"]["shell"]["status"] == "merged" and st["jobs"]["ui"]["status"] == "blocked"


def test_a_process_that_cannot_be_stopped_yet_keeps_the_run_ending(feature, monkeypatch, tmp_path):
    go = tmp_path / "go"
    monkeypatch.setenv("FAKE_HOLD", str(go))
    feature()
    drive(lambda: job_status("shell") == "working")
    st = state()
    loop._begin_end(st, loop.ENDED)
    run.save(st)
    real = proc.stop
    monkeypatch.setattr(proc, "stop", lambda *a, **k: False)
    loop.cycle()
    assert state()["status"] == "running" and state()["ending"] == loop.ENDED
    assert loop._retry_due()
    monkeypatch.setattr(proc, "stop", real)
    drive(lambda: state()["status"] == "blocked")
    go.touch()
