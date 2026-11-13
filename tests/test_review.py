import json

import pytest

from ark import euklides, review, run

D = {"action": "retry", "note": "fix it", "undo": None, "why": "", "proposal": None}


def seed(n=3, slug="euk"):
    st = {"repo": "arkimedes", "slug": slug}
    return [euklides.record(st, "core", {"trigger": "failure", "situation": f"red {i}"}, D, valid=True, usd=0.1)
            for i in range(n)]


def go(answers, *argv, distill=None):
    out, it = [], iter(answers)
    calls = []

    def fake_distill(prompt):
        calls.append(prompt)
        return distill
    args = review.parser().parse_args(list(argv))
    code = review.run_review(args, ask=lambda _: next(it), say=out.append, model=fake_distill)
    return code, "\n".join(out), calls


def test_lists_unreviewed_oldest_first_and_records_verdicts(ark_home):
    ids = seed(3)
    code, out, calls = go(["ok", "mal: should have asked", "q"], "--no-distill")
    assert code == 0 and out.index("red 0") < out.index("red 1")
    assert [(r["id"], r["verdict"], r["comment"]) for r in euklides.reviews()] == [
        (ids[0], "ok", ""), (ids[1], "wrong", "should have asked")]
    assert calls == []


def test_skip_leaves_it_for_later_and_reviewed_ones_do_not_come_back(ark_home):
    ids = seed(2)
    go(["skip", "ok"], "--no-distill")
    _, out, _ = go(["q"], "--no-distill")
    assert "red 0" in out and "red 1" not in out
    assert [r["id"] for r in euklides.reviews()] == [ids[1]]


def test_a_decision_marco_already_answered_needs_no_review(ark_home):
    ids = seed(2)
    euklides.set_marco(ids[0], "ok")
    _, out, _ = go(["q"], "--no-distill")
    assert "red 0" not in out and "red 1" in out


def test_all_shows_reviewed_ones_too_and_a_target_limits_to_one_run(ark_home):
    seed(1, slug="a")
    seed(1, slug="b")
    go(["ok", "q"], "--no-distill")
    _, out, _ = go(["q"], "--all", "arkimedes/a", "--no-distill")
    assert "arkimedes/a" in out and "arkimedes/b" not in out


def test_an_unknown_reply_is_asked_again(ark_home):
    seed(1)
    _, out, _ = go(["maybe", "mal:", "ok"], "--no-distill")
    assert euklides.reviews()[0]["verdict"] == "ok" and out.count("ok · mal: <comment> · skip · q") >= 3


def test_the_job_outcome_is_read_from_the_run_state(ark_home):
    seed(1)
    st = run.new_state({"repo": "arkimedes", "slug": "euk", "plan": "/x", "sha256": "0" * 64, "approved_at": "t"})
    st["jobs"]["core"] = {"status": "merged", "tier": "core", "attempts": 1}
    run.save(st)
    _, out, _ = go(["q"], "--no-distill")
    assert "outcome: merged" in out


def test_nothing_to_review_says_so(ark_home):
    code, out, _ = go([], "--no-distill")
    assert code == 0 and "Nothing to review" in out


NEW = "- ask before renaming a column other jobs read [arkimedes/euk/core/1]\n"


def test_new_verdicts_run_the_distill_once_and_print_the_diff(ark_home):
    seed(1)
    euklides.folder().mkdir(parents=True, exist_ok=True)
    euklides.lessons_file().write_text("- old rule [x/y/z/1]\n")
    code, out, calls = go(["mal: should have asked"], distill=NEW)
    assert code == 0 and len(calls) == 1
    assert "should have asked" in calls[0] and "- old rule [x/y/z/1]" in calls[0] and "at most 30" in calls[0]
    assert euklides.lessons_file().read_text() == NEW
    assert (euklides.folder() / "lessons.prev.md").read_text() == "- old rule [x/y/z/1]\n"
    assert "+- ask before renaming" in out and "-- old rule" in out


def test_no_new_verdict_no_distill(ark_home):
    seed(1)
    _, _, calls = go(["skip"], distill=NEW)
    assert calls == []


@pytest.mark.parametrize("bad", [None, "", "\n".join(f"- rule {i}" for i in range(31))])
def test_a_failed_distill_leaves_the_lessons_as_they_were(ark_home, bad):
    seed(1)
    euklides.folder().mkdir(parents=True, exist_ok=True)
    euklides.lessons_file().write_text("- old rule\n")
    code, out, _ = go(["ok"], distill=bad)
    assert euklides.lessons_file().read_text() == "- old rule\n" and "not changed" in out and code == 1


def test_the_distill_gets_at_most_200_decisions_newest_first(ark_home):
    ids = seed(205)
    for i in ids:
        euklides.add_review(i, "ok", "")
    from ark import distill
    p = distill.prompt()
    assert p.count('"id": ') == 200 and p.index(ids[-1]) < p.index(ids[-2])


def test_a_run_level_decision_is_listed_with_the_runs_outcome(ark_home):
    st = {"repo": "arkimedes", "slug": "euk"}
    raise_ = {"action": "raise", "note": "the run is nearly done", "undo": None, "why": "", "proposal": None,
              "usd": 0.5}
    euklides.record(st, "_run", {"trigger": "cap_run", "situation": "spent $5.00 of $4.00"}, raise_,
                    valid=True, usd=0.1)
    _, out, _ = go(["q"], "--no-distill")
    assert "job: _run" in out and "decided: raise — the run is nearly done" in out and "raised: $0.50" in out
    assert "outcome: run gone" in out
