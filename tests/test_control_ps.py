from control_helpers import SLUG, drive, job_status, settled

from ark import paths, proc


def test_a_workers_process_records_its_session_tier_and_attempt(feature, monkeypatch, tmp_path):
    feature()
    go = tmp_path / "go"
    monkeypatch.setenv("FAKE_HOLD", str(go))
    drive(lambda: job_status("shell") == "working")
    infos = proc.job_processes(paths.run_dir("demo", SLUG), "shell")
    assert [i["name"] for i in infos] == ["shell.worker"]
    meta = infos[0]["meta"]
    assert meta["tier"] == "T1" and meta["attempt"] == 1 and len(meta["session"]) == 36
    go.touch()
    drive(settled)


def test_job_processes_does_not_match_a_longer_job_name(tmp_path):
    (tmp_path / "a.worker.proc").write_text("{}")
    (tmp_path / "a-b.worker.proc").write_text("{}")
    assert proc.job_processes(tmp_path, "a") == []    # an unreadable .proc is not listed
    assert proc.job_processes(tmp_path, "c") == []
