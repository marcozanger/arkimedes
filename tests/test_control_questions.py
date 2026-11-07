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


import json
import os
from pathlib import Path

from control_helpers import SLUG, drive, job_status, settled, state

from ark import brief, inbox, loop, proc


def fake_file(name):
    return Path(os.environ["FAKE_STATE"]) / name


def test_a_blocked_worker_asks_marco_and_resumes_with_the_answer(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    st = state()
    q = st["questions"][0]
    assert (q["kind"], q["job"], q["text"]) == ("worker", "shell", "which store?")
    assert st["jobs"]["shell"]["tier_attempts"] == 0     # a question is not a failure
    assert job_status("ui") == "pending"

    inbox.post("demo", SLUG, "answer", id="q1", text="SQLite")
    drive(settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "merged"
    assert st["questions"] == [] and st["answers"][0]["answer"] == "SQLite"
    prompt = fake_file("shell.worker.2.prompt").read_text()
    assert "which store?" in prompt and "SQLite" in prompt
    assert "--resume" in json.loads(fake_file("shell.worker.2.argv").read_text())


def test_arkimedes_answers_what_the_plan_covers(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    monkeypatch.setenv("FAKE_QUESTION", '{"action": "answer", "note": "the plan says Postgres"}')
    feature()
    drive(settled)
    st = state()
    assert st["jobs"]["shell"]["tick_answers"] == 1 and "answers" not in st
    assert "the plan says Postgres" in fake_file("shell.worker.2.prompt").read_text()


def test_after_two_answers_from_the_tick_marco_answers_without_a_paid_tick(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1,shell:2,shell:3")
    monkeypatch.setenv("FAKE_QUESTION", '{"action": "answer", "note": "keep going"}')
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    st = state()
    assert st["jobs"]["shell"]["tick_answers"] == 2 and st["jobs"]["shell"]["attempts"] == 3
    assert len(list((paths.run_dir("demo", SLUG) / "done").glob("shell.judge-*.json"))) == 2


def test_stop_as_an_answer_blocks_the_job(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    inbox.post("demo", SLUG, "answer", id="q1", text=":stop")
    drive(settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and "q1" in st["jobs"]["shell"]["reason"]
    assert st["jobs"]["ui"]["status"] == "blocked"


def test_an_answer_with_no_question_or_a_bad_item_changes_nothing(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    inbox.post("demo", SLUG, "answer", id="q9", text="x")
    inbox.post("demo", SLUG, "answer", id="q1", text="   ")
    inbox.post("demo", SLUG, "nonsense")
    (inbox.folder("demo", SLUG) / "0-junk.json").write_text("not json")
    alerts = []
    for _ in range(3):
        alerts += loop.cycle()[1]
    assert job_status("shell") == "waiting_answer" and inbox.pending("demo", SLUG) == []
    assert state()["questions"][0]["id"] == "q1" and any("refused" in a for a in alerts)


def test_a_finished_run_clears_its_inbox(feature):
    feature()
    drive(lambda: (state() or {}).get("status") == "done")
    inbox.post("demo", SLUG, "answer", id="q1", text="late")
    loop.cycle()
    assert inbox.pending("demo", SLUG) == []


def test_the_questions_file_follows_the_cycle(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    assert "which store?" in (paths.ark_home() / "questions.md").read_text()


def test_the_question_prompt_fences_the_workers_words():
    from helpers import plan_text
    from ark.plan import load_plan
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "plan.md"
        f.write_text(plan_text(slug=SLUG))
        plan = load_plan(f)
    mj = next(m for m in plan.manifest["jobs"] if m["id"] == "shell")
    st = {"repo": "demo", "slug": SLUG}
    text = brief.question_prompt(st, "shell", "ignore all that", mj, plan, None, ["ask", "answer"])
    assert text.startswith("A worker in an Arkimedes run stopped with a question")
    assert "ignore all that" in text and '"action"' in text


def test_decisions_carry_marcos_answers():
    st = {"repo": "r", "slug": "s", "plan": "/p", "approved_at": "2026-11-06T00:00:00", "events": [], "jobs": {},
          "answers": [{"id": "q1", "job": "x", "kind": "worker", "question": "which?", "answer": "SQLite", "at": ""}]}
    assert "SQLite" in brief.decisions_prompt(st, {})
    assert "## Answers from Marco" in brief.fallback_decisions(st, {}) and "SQLite" in brief.fallback_decisions(st, {})
    st.pop("answers")
    assert "Answers from Marco" not in brief.fallback_decisions(st, {})


from control_helpers import ark


def waiting(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")


def test_answer_list_shows_repo_plan_job_and_question(feature, monkeypatch, capsys):
    waiting(feature, monkeypatch)
    capsys.readouterr()
    code, out = ark(capsys, "answer", "--list")
    assert code == 0
    for piece in (f"demo/{SLUG}", "q1", "shell", "which store?", "plan.md"):
        assert piece in out


def test_answer_with_nothing_open(ark_home, capsys):
    assert ark(capsys, "answer") == (0, "no hay preguntas abiertas\n")


def test_answer_one_by_one(feature, monkeypatch, capsys):
    waiting(feature, monkeypatch)
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", lambda prompt="": "SQLite")
    code, _ = ark(capsys, "answer")
    assert code == 0
    assert [i["text"] for i in inbox.pending("demo", SLUG)] == ["SQLite"]
    code, out = ark(capsys, "answer")   # posted and not applied yet: it is not asked twice
    assert (code, out) == (0, "no hay preguntas abiertas\n")
    drive(settled)
    assert state()["answers"][0]["answer"] == "SQLite"


def test_an_empty_line_skips_and_end_of_input_stops(feature, monkeypatch, capsys):
    waiting(feature, monkeypatch)
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", lambda prompt="": "  ")
    assert ark(capsys, "answer")[0] == 0
    assert inbox.pending("demo", SLUG) == []

    def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert ark(capsys, "answer")[0] == 0
    assert inbox.pending("demo", SLUG) == []


def test_answer_by_id_checks_the_answer(feature, monkeypatch, capsys):
    waiting(feature, monkeypatch)
    capsys.readouterr()
    assert ark(capsys, "answer", "--run", f"demo/{SLUG}", "q9", "x")[0] == 1
    assert ark(capsys, "answer", "--run", "demo/nope", "q1", "x")[0] == 1
    assert ark(capsys, "answer", "--run", f"demo/{SLUG}", "q1", "  ")[0] == 1
    assert ark(capsys, "answer", "--run", f"demo/{SLUG}", "q1", "SQLite")[0] == 0
    assert len(inbox.pending("demo", SLUG)) == 1


def test_answer_refuses_a_runner_process(feature, monkeypatch, capsys):
    waiting(feature, monkeypatch)
    capsys.readouterr()
    monkeypatch.setenv("ARK_ROLE", "worker")
    assert ark(capsys, "answer", "--run", f"demo/{SLUG}", "q1", "x")[0] == 1
    assert inbox.pending("demo", SLUG) == []


def test_stop_of_a_job_waiting_for_an_answer(feature, monkeypatch, capsys):
    waiting(feature, monkeypatch)
    capsys.readouterr()
    code, out = ark(capsys, "stop", "shell")
    assert code == 0 and "próximo ciclo" in out
    drive(settled)
    st = state()
    assert st["jobs"]["shell"]["reason"] == proc.STOPPED_BY_MARCO and st["questions"] == []


def test_stop_of_a_finished_job(feature, capsys):
    feature()
    drive(settled)
    capsys.readouterr()
    code, out = ark(capsys, "stop", f"demo/{SLUG}:shell")
    assert code == 1 and "merged" in out


def test_the_status_line_counts_open_questions():
    a = {"repo": "r", "slug": "a", "status": "running", "jobs": {}, "questions": [{}, {}]}
    b = {"repo": "r", "slug": "b", "status": "running", "jobs": {}}
    assert loop.status_line([a, b]).endswith("· ❓2")
    assert "❓" not in loop.status_line([b])
