import json

import pytest

from ark import cli, replay


def ask(situation, proposal="", marco="ok", trigger="blocked"):
    return {"action": "ask", "trigger": trigger, "situation": situation, "proposal": proposal, "marco": marco}


@pytest.mark.parametrize("row, verdict, kind", [
    (ask("changed files outside its area: tests/test_cli.py", "Grant tests/test_cli.py"), "own", "area"),
    (ask("changed files outside its area: packages/shell/package.json"), "R5", "public contract"),
    (ask("integration gate red, and red on ark/x without the job too", "Re-run it once"), "own", "base"),
    (ask("job gate red", "Approve $3 more for one T2 attempt"), "own", "budget"),
    (ask("the worker ended FAILED", "Accept lifecycle as done at 64d31d03 despite the 3 base e2e failures"),
     "R2", "weaker verification"),
    (ask("The mod context has no push", "Stop move-main. In plan.md, add a job `mod-bridge`"), "R1",
     "what is delivered"),
    (ask("the integration check could not run: GitError: git rev-parse"), "R6", "outside the job's branch"),
    (ask("the worker ended DONE without any change"), "?", "other"),
])
def test_each_question_is_sorted_by_what_it_was_about(row, verdict, kind):
    got = replay.classify(row)
    assert (got["verdict"], got["kind"]) == (verdict, kind)


def test_a_run_cap_within_twice_the_plan_is_his_and_past_it_is_r4():
    row = ask("the run spent $4.56, over its planned total of $4.00 and its raises", "raise 1.00", trigger="cap_run")
    assert replay.classify(row)["verdict"] == "own"
    row["proposal"] = "raise 4.00"
    assert replay.classify(row)["verdict"] == "R4"


def test_a_decision_that_was_not_a_question_stays_the_same():
    assert replay.classify({"action": "retry", "note": "n"})["verdict"] == "same"


def test_the_report_counts_and_leaves_out_invalid_answers():
    rows = [ask("changed files outside its area: a.py", "Grant a.py"),
            ask("changed files outside its area: package.json", marco="no"),
            {**ask("x"), "valid": False},
            {"action": "fixed", "note": "n"}]
    out = replay.report(rows)
    assert "4 decisions, 2 of them questions" in out
    assert "His own call now: 1** (area 1); Marco had agreed with the proposal in 1" in out
    assert "Still Marco's (reserved list): 1** (R5 1)" in out
    assert out.count("\n| ") == 3  # the header and one row per valid question
    assert "| # |" not in replay.report(rows, show_rows=False)


def test_ark_replay_reads_the_journal(ark_home, capsys):
    (ark_home / "history" / "euklides").mkdir(parents=True)
    (ark_home / "history" / "euklides" / "decisions.jsonl").write_text(
        json.dumps(ask("changed files outside its area: a.py")) + "\n")
    assert cli.main(["replay", "--summary"]) == 0
    assert "His own call now: 1" in capsys.readouterr().out
