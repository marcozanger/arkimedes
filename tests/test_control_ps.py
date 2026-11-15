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


import pytest
from control_helpers import ark

from ark import ctl, desk, run, usage


def hold(feature, monkeypatch, tmp_path, **env):
    feature()
    go = tmp_path / "go"
    monkeypatch.setenv("FAKE_HOLD", str(go))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    drive(lambda: job_status("shell") == "working")
    return go


def test_ps_shows_job_status_tokens_and_last_message(feature, monkeypatch, tmp_path, capsys):
    go = hold(feature, monkeypatch, tmp_path, FAKE_USAGE="12500")
    log = paths.run_dir("demo", SLUG) / "shell.worker.log"
    drive(lambda: usage.scan(log)["tokens"] > 0)
    capsys.readouterr()
    code, out = ark(capsys, "ps")
    assert code == 0
    line = next(x for x in out.splitlines() if " shell " in f" {x} ")
    assert f"demo/{SLUG}" in line and "working" in line and "12.5k" in line and "working on it" in line
    assert not any(" ui " in f" {x} " and "working" in x for x in out.splitlines())
    go.touch()
    drive(settled)


def test_ps_prints_a_header_line_per_live_run(feature, monkeypatch, tmp_path, capsys):
    go = hold(feature, monkeypatch, tmp_path)
    capsys.readouterr()
    code, out = ark(capsys, "ps")
    lines = out.splitlines()
    i = next(n for n, x in enumerate(lines) if x.startswith(f"demo/{SLUG} · running · elapsed "))
    assert " · eta " in lines[i]
    assert i < next(n for n, x in enumerate(lines) if x.startswith("RUN"))
    assert i < next(n for n, x in enumerate(lines) if " shell " in f" {x} ")
    go.touch()
    drive(settled)


def test_ps_header_uses_desk_times(monkeypatch):
    monkeypatch.setattr(desk, "_times", lambda repo, slug, now: ("1h12m", "~40m"))
    st = {"repo": "demo", "slug": "x", "status": "running"}
    assert ctl._ps_header(st, None) == "demo/x · running · elapsed 1h12m · eta ~40m"


def test_ps_with_nothing_running(ark_home, capsys):
    assert ark(capsys, "ps") == (0, "nothing running\n")


def test_logs_render_the_live_log_then_the_archived_one(feature, monkeypatch, tmp_path, capsys):
    go = hold(feature, monkeypatch, tmp_path, FAKE_USAGE="1")
    log = paths.run_dir("demo", SLUG) / "shell.worker.log"
    drive(lambda: usage.scan(log)["tokens"] > 0)
    capsys.readouterr()
    code, out = ark(capsys, "logs", "shell")
    assert code == 0 and "working on it" in out
    go.touch()
    drive(settled)
    capsys.readouterr()
    code, out = ark(capsys, "logs", f"demo/{SLUG}:shell")   # the run may have closed: the long form still finds it
    assert code == 0 and "working on it" in out and "ended" in out


def test_stop_ends_a_job_blocked(feature, monkeypatch, tmp_path, capsys):
    go = hold(feature, monkeypatch, tmp_path)
    capsys.readouterr()
    code, out = ark(capsys, "stop", "shell")
    assert code == 0 and "stopped" in out
    drive(settled)
    st = run.load("demo", SLUG)
    assert st["jobs"]["shell"]["status"] == "blocked" and st["jobs"]["shell"]["reason"] == proc.STOPPED_BY_MARCO
    go.touch()


def test_stop_is_refused_to_a_runner_process(feature, monkeypatch, tmp_path, capsys):
    go = hold(feature, monkeypatch, tmp_path)
    capsys.readouterr()
    monkeypatch.setenv("ARK_ROLE", "worker")
    code, out = ark(capsys, "stop", "shell")
    assert code == 1 and "runner" in out
    monkeypatch.delenv("ARK_ROLE")
    go.touch()
    drive(settled)


def test_resolve(monkeypatch):
    monkeypatch.setattr(ctl, "_states", lambda: [
        {"repo": "a", "slug": "x", "status": "running", "jobs": {"one": {}, "both": {}}},
        {"repo": "b/c", "slug": "y", "status": "running", "jobs": {"both": {}}},
        {"repo": "d", "slug": "z", "status": "done", "jobs": {"old": {}}}])
    assert ctl._resolve("one") == ("a", "x", "one")
    assert ctl._resolve("b/c/y:both") == ("b/c", "y", "both")
    assert ctl._resolve("d/z:old") == ("d", "z", "old")
    with pytest.raises(ctl.CtlError, match="a/x:both"):
        ctl._resolve("both")
    with pytest.raises(ctl.CtlError, match="old"):
        ctl._resolve("old")
    with pytest.raises(ctl.CtlError, match="nope"):
        ctl._resolve("a/x:nope")


def test_ps_header_shows_the_base_main_a_red_main_and_a_live_refresh():
    st = {"repo": "demo", "slug": "x", "status": "running", "base_sha": "1" * 40,
          "refreshes": [{"n": 0, "main": "2" * 40, "result": "landed"},
                        {"n": 1, "main": "3" * 40, "result": "landed"},
                        {"n": 2, "main": "4" * 40, "result": "failed"}],
          "main_red": "5" * 40, "refresh": {"n": 3, "main": "6" * 40, "status": "needs_worker"}}
    head = ctl._ps_extra(st)
    assert "base: main@33333333" in head and "main red at 55555555" in head and "refresh 3: needs_worker" in head
    assert "base: main@11111111" in ctl._ps_extra({"base_sha": "1" * 40})
    assert "red" not in ctl._ps_extra({"base_sha": "1" * 40})


def test_ps_says_main_is_red_instead_of_the_waiting_text():
    j = {"status": "waiting_dep", "waiting_on": "shell", "tier": "T1"}
    st = {"repo": "demo", "slug": "x", "jobs": {"ui": j}, "base_green": False, "main_red": "5" * 40}
    assert ctl._ps_row(st, "ui", j)[-1] == "waiting: main is red"
    st["main_red"] = None
    assert ctl._ps_row(st, "ui", j)[-1] == "waiting on shell"
