from test_monitor_runs import make_run

from ark import monitor, paths, questions, run


def test_questions_answers_and_spec_text_in_the_snapshot(make_feature):
    make_run(make_feature)
    st = run.load("demo", "notes-edit")
    questions.ask(st, "worker", "which db?", job="shell")
    q2 = questions.ask(st, "cap_run", "over budget", job=None)
    questions.resolve(st, q2["id"], "raise 5")
    st["answers"][0]["by"] = "ui"
    questions.ask(st, "worker", "second?", job="ui")
    run.save(st)
    [p] = monitor.build()["plans"]
    assert [(q["id"], q["kind"], q["job"], q["text"]) for q in p["questions"]] == [
        ("q1", "worker", "shell", "which db?"), ("q3", "worker", "ui", "second?")]
    assert set(p["questions"][0]) == {"id", "kind", "job", "text", "asked"}
    [a] = p["answers"]
    assert (a["id"], a["kind"], a["question"], a["answer"], a["by"]) == ("q2", "cap_run", "over budget", "raise 5", "ui")
    assert set(a) == {"id", "kind", "job", "question", "answer", "asked", "at", "by"}
    assert p["spec_file"].startswith("---") and "# Spec" in p["spec_file"]


def test_by_is_none_when_absent_and_no_spec_is_empty(make_feature):
    p = make_run(make_feature)
    st = run.load("demo", "notes-edit")
    q = questions.ask(st, "worker", "x?", job="shell")
    questions.resolve(st, q["id"], "yes")
    run.save(st)
    (p.parent / "spec.md").unlink()
    [out] = monitor.build()["plans"]
    assert out["answers"][0]["by"] is None and out["spec_file"] == ""


def test_empty_defaults(make_feature):
    make_run(make_feature, with_state=False)
    [p] = monitor.build()["plans"]
    assert p["questions"] == [] and p["answers"] == []
    assert monitor._empty("r", "s")["spec_file"] == ""
