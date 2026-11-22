import pytest

from ark import answers, inbox, questions, run


def _live_run(ark_home, kind="worker", answered=None, status="running"):
    st = run.new_state({"repo": "demo", "slug": "notes-edit", "plan": "/p/plan.md", "sha256": "0" * 64,
                        "approved_at": "2026-11-05T10:00:00+00:00"})
    st["status"] = status
    q = questions.ask(st, kind, "which store?", job="x" if kind == "worker" else None)
    if answered:
        questions.resolve(st, q["id"], answered)
    run.save(st)
    return "demo", "notes-edit", q["id"]


@pytest.fixture
def live_run_with_question(ark_home):
    return _live_run(ark_home)


@pytest.fixture
def live_run_with_cap_question(ark_home):
    return _live_run(ark_home, kind="cap_run")


@pytest.fixture
def run_with_answer(ark_home):
    return (*_live_run(ark_home, answered="SQLite"), "SQLite")


def test_posts_an_open_question(live_run_with_question):
    repo, slug, qid = live_run_with_question
    answers.post(repo, slug, qid, "use the second option")
    assert [i["id"] for i in inbox.pending(repo, slug) if i["kind"] == "answer"] == [qid]


def test_twice_is_refused(live_run_with_question):
    repo, slug, qid = live_run_with_question
    answers.post(repo, slug, qid, "a")
    with pytest.raises(answers.AnswerError) as e:
        answers.post(repo, slug, qid, "b")
    assert e.value.kind == "posted"


def test_an_answered_question_returns_its_answer(run_with_answer):
    repo, slug, qid, text = run_with_answer
    with pytest.raises(answers.AnswerError) as e:
        answers.post(repo, slug, qid, "late")
    assert e.value.kind == "closed" and e.value.answer["answer"] == text


def test_a_rejected_answer(live_run_with_cap_question):
    repo, slug, qid = live_run_with_cap_question
    with pytest.raises(answers.AnswerError) as e:
        answers.post(repo, slug, qid, "maybe")
    assert e.value.kind == "rejected"
    assert inbox.pending(repo, slug) == []


def test_a_runner_process_cannot_answer(live_run_with_question, monkeypatch):
    monkeypatch.setenv("ARK_ROLE", "worker")
    with pytest.raises(answers.AnswerError) as e:
        answers.post(*live_run_with_question, "x")
    assert e.value.kind == "role"


def _two_options(ark_home):
    st = run.new_state({"repo": "demo", "slug": "notes-edit", "plan": "/p/plan.md", "sha256": "0" * 64,
                        "approved_at": "2026-11-05T10:00:00+00:00"})
    st["status"] = "running"
    q = questions.ask(st, "worker", "go on?", job="x",
                      options=[{"label": "Go on", "detail": "the proposal", "answer": "ok"},
                               {"label": "Stop the job", "detail": "x ends blocked", "answer": ":stop",
                                "confirm": True}])
    run.save(st)
    return "demo", "notes-edit", q["id"]


def test_an_option_number_posts_its_answer_text(ark_home):
    repo, slug, qid = _two_options(ark_home)
    assert answers.post(repo, slug, qid, " 1 ")["text"] == "ok"
    assert [i["text"] for i in inbox.pending(repo, slug)] == ["ok"]


def test_a_number_past_the_last_option_is_rejected(ark_home):
    repo, slug, qid = _two_options(ark_home)
    with pytest.raises(answers.AnswerError) as e:
        answers.post(repo, slug, qid, "9")
    assert e.value.kind == "rejected" and "no option 9" in str(e.value)
    assert inbox.pending(repo, slug) == []


def test_a_number_without_options_is_plain_text(live_run_with_question):
    repo, slug, qid = live_run_with_question
    answers.post(repo, slug, qid, "2")
    assert [i["text"] for i in inbox.pending(repo, slug)] == ["2"]
