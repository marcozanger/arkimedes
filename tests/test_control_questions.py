import time

import pytest

from ark import paths, questions, run


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
    assert (q["kind"], q["job"]) == ("worker", "shell") and q["text"].startswith("which store?")
    assert "Proposal: :stop" in q["text"]                # what Euklides would do, for Marco to take with ok
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


def test_euklides_answers_what_only_the_job_risks(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "answer", "note": "SQLite", "undo": "job"}')
    feature()
    drive(settled)
    st = state()
    assert st["jobs"]["shell"]["euklides_count"] == 1 and "answers" not in st
    prompt = fake_file("shell.worker.2.prompt").read_text()
    assert "which store?" in prompt and "SQLite" in prompt


def test_an_answer_that_reaches_the_plan_waits_for_marcos_ok(feature, monkeypatch):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "answer", "note": "SQLite", "undo": "plan", "why": "ui reads it"}')
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    q = state()["questions"][0]
    assert "Proposal: SQLite" in q["text"]
    inbox.post("demo", SLUG, "answer", id=q["id"], text="ok")
    drive(settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "merged"
    assert "SQLite" in fake_file("shell.worker.2.prompt").read_text()


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
    assert ark(capsys, "answer") == (0, "no open questions\n")


def test_answer_one_by_one(feature, monkeypatch, capsys):
    waiting(feature, monkeypatch)
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", lambda prompt="": "SQLite")
    code, _ = ark(capsys, "answer")
    assert code == 0
    assert [i["text"] for i in inbox.pending("demo", SLUG)] == ["SQLite"]
    code, out = ark(capsys, "answer")   # posted and not applied yet: it is not asked twice
    assert (code, out) == (0, "no open questions\n")
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
    assert code == 0 and "next cycle" in out
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


GOOD_QUESTION = {"id": "q1", "kind": "worker", "job": "shell", "text": "which?", "asked": ""}


def test_load_takes_only_questions_of_the_right_shape(feature):
    feature()
    loop.cycle()
    st = state()
    for good in ([], [GOOD_QUESTION], [{**GOOD_QUESTION, "job": None}]):
        run.save({**st, "questions": good})
        assert loop._load("demo", SLUG)["questions"] == good
    for bad in (None, 5, "q1", {}, [5], [{"id": "q1"}], [{**GOOD_QUESTION, "id": 1}], [{**GOOD_QUESTION, "kind": None}],
                [{**GOOD_QUESTION, "text": 5}], [{**GOOD_QUESTION, "job": 5}], [GOOD_QUESTION, []],
                [{k: v for k, v in GOOD_QUESTION.items() if k != "job"}]):
        run.save({**st, "questions": bad})
        with pytest.raises(run.StateCorrupt):
            loop._load("demo", SLUG)


@pytest.mark.parametrize("damage", [5, [{"id": "q1"}]])
def test_a_run_with_malformed_questions_is_skipped_and_the_others_advance(feature, damage):
    feature(repo="other")
    loop.cycle()
    run.save({**state("other"), "questions": damage})
    feature()
    alerts = []
    for _ in range(400):  # not drive(): each alert comes once, and drive's own cycles would drop it
        alerts += loop.cycle()[1]
        if state()["status"] == "done":
            break
        time.sleep(0.05)
    assert state()["status"] == "done"
    assert any("other/" in a and "skipped until its state is repaired" in a for a in alerts)
    assert any(f"demo/{SLUG}: run done" in a for a in alerts)
    assert state("other")["questions"] == damage         # only Marco can say what it should have been


SURROGATE = "bad \ud800 text"


def test_questions_md_can_always_be_written(ark_home):
    q = {**GOOD_QUESTION, "id": SURROGATE, "job": SURROGATE, "text": SURROGATE}
    st = {"repo": SURROGATE, "slug": SURROGATE, "plan": SURROGATE, "questions": [q]}
    questions.save_file([st])
    assert "bad ? text" in (paths.ark_home() / "questions.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("damage", [
    {"questions": [{**GOOD_QUESTION, "text": SURROGATE}]},
    {"questions": [{**GOOD_QUESTION, "job": SURROGATE}]},
    {"plan": SURROGATE, "questions": [GOOD_QUESTION]},
    {"alerts": [SURROGATE]},
    {"events": [SURROGATE]},
    {"reason": SURROGATE},
], ids=["text", "job", "plan", "alert", "event", "reason"])
def test_a_lone_surrogate_in_a_state_stops_nothing(feature, damage):
    feature(repo="other")
    loop.cycle()
    run.state_file("other", SLUG).write_text(json.dumps({**state("other"), **damage}), encoding="utf-8")
    feature()
    alerts = []
    for _ in range(400):
        alerts += loop.cycle()[1]
        if state()["status"] == "done":
            break
        time.sleep(0.05)
    assert state()["status"] == "done"
    assert any(f"demo/{SLUG}: run done" in a for a in alerts)
    assert not any("cannot be saved" in a for a in alerts)   # it would only reap from then on
    "\n".join(alerts).encode("utf-8")
    (paths.runs_dir() / "alerts.jsonl").read_text(encoding="utf-8")


def test_an_alert_that_never_was_in_a_state_is_delivered_clean(feature, monkeypatch):
    feature()
    monkeypatch.setattr(loop, "_orphans", lambda approved, alerts: alerts.append(SURROGATE))
    assert "bad ? text" in loop.cycle()[1]
    assert "bad ? text" in (paths.runs_dir() / "alerts.jsonl").read_text(encoding="utf-8")
