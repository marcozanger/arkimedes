import json

from control_helpers import drive, state

from ark import notify, paths

ST = {"repo": "demo", "slug": "notes-edit", "jobs": {"shell": {}, "ui": {}}}


def lines():
    return [json.loads(x) for x in (paths.runs_dir() / "alerts.jsonl").read_text().splitlines()]


def test_an_alert_that_starts_with_a_job_names_it():
    assert notify.origin(ST, "demo/notes-edit: shell blocked: gate failed") == \
        {"repo": "demo", "slug": "notes-edit", "job": "shell"}
    assert notify.origin(ST, "demo/notes-edit: ui: the integration check could not run")["job"] == "ui"


def test_an_alert_about_the_whole_run_names_no_job():
    assert notify.origin(ST, "demo/notes-edit: run blocked: x")["job"] == ""
    assert notify.origin(ST, "demo/notes-edit: shellfish waits")["job"] == ""
    assert notify.origin(ST, "demo/notes-edit: question q1 (shell): why?")["job"] == ""


def test_recorded_lines_carry_their_origin(ark_home):
    a = "demo/notes-edit: shell blocked: x"
    assert notify.deliver([a, "daily spending cap reached"], [], {a: notify.origin(ST, a)}) is None
    got = lines()
    assert (got[0]["repo"], got[0]["slug"], got[0]["job"], got[0]["text"]) == ("demo", "notes-edit", "shell", a)
    assert (got[1]["repo"], got[1]["slug"], got[1]["job"]) == ("", "", "")


def test_without_origins_the_fields_are_empty(ark_home):
    notify.deliver(["a"], ["a"])
    assert lines() == [{"at": lines()[0]["at"], "text": "a", "critical": True, "repo": "", "slug": "", "job": ""}]


def test_a_runs_alerts_reach_the_file_with_its_plan(feature):
    feature()
    drive(lambda: (state() or {}).get("status") in ("done", "blocked"))
    mine = [x for x in lines() if (x["repo"], x["slug"]) == ("demo", "notes-edit")]
    assert any(x["text"].startswith("demo/notes-edit: run ") for x in mine)
