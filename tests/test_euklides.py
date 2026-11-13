import json

import pytest

from ark import euklides, history


def job(**over):
    return {"tier": "T1", "usd": 0.5, "euklides_count": 0, **over}


@pytest.mark.parametrize("trigger, j, budget, expected", [
    ("failure", job(), 2, ["retry", "escalate", "fixed", "ask"]),
    ("failure", job(tier="T2"), 2, ["retry", "fixed", "ask"]),
    ("failure", job(usd=2.0), 2, ["fixed", "ask"]),
    ("question", job(), 2, ["answer", "fixed", "ask"]),
    ("question", job(usd=9.0), 2, ["answer", "fixed", "ask"]),
    ("blocked", job(), 2, ["unblock", "fixed", "ask"]),
    ("blocked", job(usd=2.5), 2, ["fixed", "ask"]),
    ("failure", job(euklides_count=4), 2, ["ask"]),
    ("question", job(euklides_count=4), 2, ["ask"]),
])
def test_allowed_actions(trigger, j, budget, expected):
    assert euklides.allowed(trigger, j, budget) == expected


def test_decisions_per_job_comes_from_the_settings(ark_home):
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "euklides": {"decisions_per_job": 1}}))
    assert euklides.allowed("failure", job(euklides_count=1), 2) == ["ask"]
    assert euklides.allowed("failure", job(euklides_count=0), 2) == ["retry", "escalate", "fixed", "ask"]


def test_a_job_without_a_count_has_made_no_decision():
    assert euklides.allowed("failure", {"tier": "T0", "usd": 0.0}, 1) == ["retry", "escalate", "fixed", "ask"]


def test_fixed_is_read_with_its_note():
    assert euklides.parse({"action": "fixed", "note": "moved the file into the area"}, ["fixed", "ask"]) == {
        "action": "fixed", "note": "moved the file into the area", "undo": None, "why": "", "proposal": None}
    assert euklides.parse({"action": "fixed"}, ["fixed", "ask"]) is None
    assert euklides.parse({"action": "fixed", "note": "x"}, ["ask"]) is None


def d(**fields):
    return {"action": "retry", "note": "fix the import", **fields}


def test_a_minimal_answer_is_read():
    assert euklides.parse(d(), ["retry", "ask"]) == {
        "action": "retry", "note": "fix the import", "undo": None, "why": "", "proposal": None}


def test_an_answer_to_the_worker_keeps_its_undo_and_why():
    got = euklides.parse({"action": "answer", "note": "use SQLite", "undo": "job", "why": "local choice"},
                         ["answer", "ask"])
    assert got == {"action": "answer", "note": "use SQLite", "undo": "job", "why": "local choice", "proposal": None}


def test_an_answer_whose_mistake_reaches_the_plan_becomes_a_question_with_the_answer_as_proposal():
    got = euklides.parse({"action": "answer", "note": "rename the column", "undo": "plan", "why": "T3 reads it"},
                         ["answer", "ask"])
    assert got["action"] == "ask" and got["proposal"] == "rename the column" and got["undo"] == "plan"
    assert "T3 reads it" in got["note"]


@pytest.mark.parametrize("raw", [
    None, "retry", [], {"action": "retry"}, {"note": "x"},
    d(action="block"), d(action="escalate"),                      # not allowed now
    d(note=""), d(note="   "), d(note=3),
    d(extra=1),                                                   # unknown key
    {"action": "answer", "note": "x"},                            # answer without undo
    {"action": "answer", "note": "x", "undo": "maybe"},
    {"action": "ask", "note": "x"},                               # ask without proposal
    {"action": "ask", "note": "x", "proposal": "  "},
    d(why=3), d(proposal=["x"]),
])
def test_anything_else_is_not_usable(raw):
    assert euklides.parse(raw, ["retry", "answer", "ask"]) is None


def test_long_fields_are_cut():
    got = euklides.parse(d(note="n" * 5000, why="w" * 5000), ["retry"])
    assert len(got["note"]) == 1000 and len(got["why"]) == 300


def st():
    return {"repo": "arkimedes", "slug": "euklides"}


DECISION = {"action": "retry", "note": "fix it", "undo": None, "why": "", "proposal": None}


def test_each_decision_appends_one_line_with_a_unique_id(ark_home):
    a = euklides.record(st(), "core", {"trigger": "failure", "situation": "red " * 400}, DECISION, valid=True, usd=0.8)
    b = euklides.record(st(), "core", {"trigger": "failure", "situation": "red"}, DECISION, valid=False, usd=0)
    c = euklides.record(st(), "cli", {"trigger": "question", "situation": "which?"}, DECISION, valid=True, usd=0.1)
    assert (a, b, c) == ("arkimedes/euklides/core/1", "arkimedes/euklides/core/2", "arkimedes/euklides/cli/1")
    rows = euklides.decisions()
    assert [r["id"] for r in rows] == [a, b, c]
    assert len(rows[0]["situation"]) == euklides.SITUATION_CHARS and rows[1]["valid"] is False
    assert rows[0]["marco"] is None and rows[0]["usd"] == 0.8 and rows[0]["job"] == "core"
    assert set(rows[0]) == {"id", "at", "repo", "slug", "job", "trigger", "situation", "action", "note", "undo",
                            "why", "proposal", "valid", "usd", "marco"}


def test_marco_answer_rewrites_only_its_line(ark_home):
    a = euklides.record(st(), "core", {"trigger": "question", "situation": "q"}, DECISION, valid=True, usd=0)
    b = euklides.record(st(), "core", {"trigger": "question", "situation": "q"}, DECISION, valid=True, usd=0)
    euklides.set_marco(b, "ok")
    rows = {r["id"]: r for r in euklides.decisions()}
    assert rows[a]["marco"] is None and rows[b]["marco"] == "ok"


def test_set_marco_of_an_unknown_id_changes_nothing(ark_home):
    euklides.record(st(), "core", {"trigger": "question", "situation": "q"}, DECISION, valid=True, usd=0)
    before = (history.history_dir() / "euklides" / "decisions.jsonl").read_text()
    euklides.set_marco("nope/x/y/1", "ok")
    assert (history.history_dir() / "euklides" / "decisions.jsonl").read_text() == before


def test_reviews_append_and_read_back(ark_home):
    euklides.add_review("a/b/c/1", "ok", "")
    euklides.add_review("a/b/c/2", "wrong", "should have asked")
    assert [(r["id"], r["verdict"], r["comment"]) for r in euklides.reviews()] == [
        ("a/b/c/1", "ok", ""), ("a/b/c/2", "wrong", "should have asked")]


def test_lessons_are_empty_when_missing_and_read_when_present(ark_home):
    assert euklides.lessons() == ""
    euklides.lessons_file().parent.mkdir(parents=True)
    euklides.lessons_file().write_text("- ask before renaming a column [a/b/c/1]\n")
    assert euklides.lessons() == "- ask before renaming a column [a/b/c/1]"


def test_a_journal_line_that_is_not_json_is_skipped(ark_home):
    euklides.record(st(), "core", {"trigger": "question", "situation": "q"}, DECISION, valid=True, usd=0)
    with open(history.history_dir() / "euklides" / "decisions.jsonl", "a") as f:
        f.write("not json\n")
    assert len(euklides.decisions()) == 1


# ---- raise: Euklides may add budget, up to a share of what was planned -------------------------

def test_a_raise_is_read_with_its_usd():
    got = euklides.parse({"action": "raise", "usd": 0.4, "note": "one more attempt will do"}, ["raise", "ask"])
    assert got == {"action": "raise", "note": "one more attempt will do", "undo": None, "why": "",
                   "proposal": None, "usd": 0.4}


@pytest.mark.parametrize("raw", [
    {"action": "raise", "note": "x"},                              # no usd
    {"action": "raise", "note": "x", "usd": 0},
    {"action": "raise", "note": "x", "usd": -1},
    {"action": "raise", "note": "x", "usd": True},
    {"action": "raise", "note": "x", "usd": "0.4"},
    {"action": "raise", "note": "x", "usd": float("inf")},
    {"action": "raise", "note": "x", "usd": float("nan")},
    {"action": "retry", "note": "x", "usd": 0.4},                  # usd only with a raise
])
def test_a_raise_without_a_positive_usd_is_not_usable(raw):
    assert euklides.parse(raw, ["raise", "retry", "ask"]) is None


def test_a_raise_is_not_usable_when_it_is_not_open():
    assert euklides.parse({"action": "raise", "note": "x", "usd": 0.1}, ["retry", "ask"]) is None


@pytest.mark.parametrize("j, margin, expected", [
    (job(usd=1.0), 0.5, ["raise", "fixed", "ask"]),                 # at its budget, margin left
    (job(usd=1.2), 0.5, ["raise", "fixed", "ask"]),                 # over it
    (job(usd=0.5), 0.5, ["retry", "escalate", "fixed", "ask"]),     # under it: no raise
    (job(usd=1.2), 0, ["fixed", "ask"]),                            # no margin at all
    (job(usd=1.6, usd_raised=0.5), 0.5, ["fixed", "ask"]),          # the margin is spent
    (job(usd=1.2, usd_raised=0.25), 0.5, ["retry", "escalate", "fixed", "ask"]),  # under budget + raised
    (job(usd=1.3, usd_raised=0.25), 0.5, ["raise", "fixed", "ask"]),  # over again, $0.25 left
])
def test_raise_opens_at_or_over_the_budget_while_margin_is_left(j, margin, expected):
    assert euklides.allowed("failure", j, 1, margin) == expected


def test_marcos_raise_counts_for_the_budget_but_not_against_the_margin():
    j = job(usd=1.5, usd_raised=0.6, marco_raised=0.6)
    assert euklides.margin_left(j, 1, 0.5) == 0.5
    assert euklides.allowed("blocked", j, 1, 0.5) == ["unblock", "fixed", "ask"]
    assert euklides.allowed("blocked", job(usd=1.6, usd_raised=0.6), 1, 0.5) == ["fixed", "ask"]


def test_the_prompt_states_the_budget_what_was_raised_and_the_margin_left():
    from test_euklides_brief import euk
    p = euk(allowed=["raise", "ask"], usd_raised=0.25, margin_left=0.25)
    assert "planned $2" in p and "raised $0.25" in p and "margin left $0.25" in p
    assert "`raise`" in p and "a total" in p
