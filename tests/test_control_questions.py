from ark import paths, questions


def blank():
    return {"repo": "r", "slug": "s", "plan": "/p/plan.md", "status": "running", "jobs": {},
            "alerts": [], "events": [], "updated": ""}


def test_asking_twice_is_one_question():
    st = blank()
    a = questions.ask(st, "worker", "which store?", job="x")
    b = questions.ask(st, "worker", "which store, again?", job="x")
    assert a is b and a["id"] == "q1" and len(st["questions"]) == 1
    assert len(st["alerts"]) == 1 and "which store?" in st["alerts"][0] and "ark answer" in st["alerts"][0]
    assert questions.ask(st, "worker", "other", job="y")["id"] == "q2"


def test_a_quiet_question_makes_no_alert_and_a_critical_one_is_kept_apart():
    st = blank()
    questions.ask(st, "plan_changed", "held", quiet=True)
    assert st["alerts"] == [] and "critical" not in st
    questions.ask(st, "cap_run", "over", critical=True)
    assert len(st["critical"]) == 1


def test_question_text_is_cleaned_and_bounded():
    st = blank()
    q = questions.ask(st, "worker", "\x1b[31mhi\x07 " + "x" * 5000, job="x")
    assert "\x1b" not in q["text"] and "\x07" not in q["text"] and len(q["text"]) == questions.MAX_TEXT


def test_resolve_moves_it_to_the_answers():
    st = blank()
    q = questions.ask(st, "worker", "which?", job="x")
    questions.resolve(st, q["id"], "  SQLite  ")
    assert st["questions"] == [] and questions.find(st, q["id"]) is None
    a = st["answers"][0]
    assert (a["id"], a["job"], a["question"], a["answer"]) == ("q1", "x", "which?", "SQLite")
    assert questions.ask(st, "worker", "next", job="x")["id"] == "q2"   # ids are not reused


def test_drop_kind_filters_by_job():
    st = blank()
    questions.ask(st, "worker", "a", job="x")
    questions.ask(st, "worker", "b", job="y")
    questions.drop_kind(st, "worker", job="x")
    assert [q["job"] for q in st["questions"]] == ["y"]
    questions.drop_kind(st, "worker")
    assert st["questions"] == [] and "answers" not in st


def test_check_answer():
    worker = {"kind": "worker"}
    cap = {"kind": "cap_run"}
    plan = {"kind": "plan_changed"}
    assert questions.check_answer(worker, "SQLite") is None
    assert questions.check_answer(worker, ":stop") is None
    assert questions.check_answer(worker, "   ")
    assert questions.check_answer(cap, "stop") is None
    assert questions.check_answer(cap, "raise 5.5") is None
    for bad in ("raise", "raise -1", "raise 0", "raise nan", "raise inf", "raise 5 6", "yes"):
        assert questions.check_answer(cap, bad), bad
    assert questions.check_answer(plan, "stop") is None
    assert questions.check_answer(plan, "go on")


def test_the_file_lists_open_questions_and_goes_back_to_empty(ark_home):
    st = blank()
    q = questions.ask(st, "worker", "which store?", job="x")
    questions.save_file([st])
    text = (paths.ark_home() / "questions.md").read_text()
    assert "r/s" in text and "q1" in text and "which store?" in text and "/p/plan.md" in text and "x" in text
    questions.resolve(st, q["id"], "a")
    questions.save_file([st])
    assert "which store?" not in (paths.ark_home() / "questions.md").read_text()


def test_no_questions_and_no_file_writes_nothing(ark_home):
    questions.save_file([blank()])
    assert not (paths.ark_home() / "questions.md").exists()


def test_the_file_is_replaced_not_written_through(ark_home, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (paths.ark_home() / "questions.md").symlink_to(victim)
    st = blank()
    questions.ask(st, "worker", "which?", job="x")
    questions.save_file([st])
    assert victim.read_text() == "keep"
    assert not (paths.ark_home() / "questions.md").is_symlink()
