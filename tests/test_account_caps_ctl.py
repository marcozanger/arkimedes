from control_helpers import ark

from ark import daycap, questions


def open_one(ark_home):
    d = daycap.load()
    daycap.ask(d, "globallogic", "globallogic spent $31.00 today; its cap is $30.00.")
    daycap.save(d)


def test_answer_list_shows_account_questions(ark_home, capsys):
    open_one(ark_home)
    code, out = ark(capsys, "answer", "--list")
    assert code == 0 and "account globallogic · c1" in out


def test_answer_cap_posts_to_the_account_inbox(ark_home, capsys):
    open_one(ark_home)
    assert ark(capsys, "answer", "--cap", "c1", "more please")[0] == 1
    assert ark(capsys, "answer", "--cap", "c1", "raise 20")[0] == 0
    assert [i["text"] for i in daycap.pending()] == ["raise 20"]
    assert ark(capsys, "answer", "--cap", "c9", "stop")[0] == 1


def test_a_posted_answer_is_not_listed_again(ark_home, capsys):
    open_one(ark_home)
    ark(capsys, "answer", "--cap", "c1", "stop")
    assert "c1" not in ark(capsys, "answer", "--list")[1]


def test_interactive_answers_account_questions_first(ark_home, capsys, monkeypatch):
    open_one(ark_home)
    monkeypatch.setattr("builtins.input", lambda _: "raise 5")
    assert ark(capsys, "answer")[0] == 0
    assert [i["text"] for i in daycap.pending()] == ["raise 5"]


def test_a_runner_process_cannot_answer(ark_home, capsys, monkeypatch):
    open_one(ark_home)
    monkeypatch.setenv("ARK_ROLE", "worker")
    assert ark(capsys, "answer", "--cap", "c1", "raise 20")[0] == 1
    assert daycap.pending() == []


def test_ps_counts_account_questions(ark_home, capsys):
    open_one(ark_home)
    out = ark(capsys, "ps")[1]
    assert "globallogic" in out and "ark answer" in out and "nothing running" not in out


def test_questions_md_lists_account_questions():
    text = questions.render([], [{"id": "c1", "account": "globallogic", "text": "t"}])
    assert "## account globallogic · c1" in text and "None open." not in text


def test_questions_md_is_written_for_account_questions_alone(ark_home):
    questions.save_file([], [{"id": "c1", "account": "globallogic", "text": "t"}])
    assert "account globallogic" in (ark_home / "questions.md").read_text()
