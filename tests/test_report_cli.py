import json

from control_helpers import ark
from report_helpers import receipt, result_line, run_state


def save(st):
    from ark import run
    run.save(st)


def test_report_a_closed_run_writes_report_steps_and_history(ark_home, capsys):
    from ark import history, paths
    receipt("shell.worker", 0, 10, log=result_line())
    save(run_state())
    code, out = ark(capsys, "report", "demo/notes-edit")
    d = paths.run_dir("demo", "notes-edit")
    assert code == 0 and str(d / "report.html") in out
    assert (d / "report.html").exists() and (d / "steps.jsonl").exists()
    assert len((history.history_dir() / "jobs.jsonl").read_text().splitlines()) == 2
    assert (history.history_dir() / "insights.md").exists()


def test_a_run_still_going_gets_a_report_but_no_history(ark_home, capsys):
    from ark import history
    save(run_state(status="running", closing=None))
    code, out = ark(capsys, "report", "demo/notes-edit")
    assert code == 0 and "sin historial" in out
    assert not (history.history_dir() / "jobs.jsonl").exists()


def test_no_run_is_an_error(ark_home, capsys):
    code, out = ark(capsys, "report", "demo/nothing")
    assert code == 1 and "no hay corrida" in out


def test_a_runner_process_cannot_write_history(ark_home, capsys, monkeypatch):
    save(run_state())
    monkeypatch.setenv("ARK_ROLE", "worker")
    code, out = ark(capsys, "report", "demo/notes-edit")
    assert code == 2 and "ARK_ROLE" in out


def test_open_opens_the_file(ark_home, capsys, monkeypatch):
    from ark import reporting
    opened = []
    monkeypatch.setattr(reporting, "open_file", opened.append)
    save(run_state())
    ark(capsys, "report", "demo/notes-edit", "--open")
    assert [p.name for p in opened] == ["report.html"]


def test_all_reports_every_closed_run(ark_home, capsys):
    from ark import history
    save(run_state())
    save(run_state(slug="other"))
    save(run_state(slug="going", status="running", closing=None))
    code, out = ark(capsys, "report", "--all")
    plans = {json.loads(x)["plan"] for x in (history.history_dir() / "plans.jsonl").read_text().splitlines()}
    assert code == 0 and plans == {"notes-edit", "other"}


def test_stats_rewrites_insights(ark_home, capsys):
    code, out = ark(capsys, "stats")
    assert code == 0 and "insights.md" in out
