import json

from report_helpers import done_dir, iso, job, receipt, result_line, run_state


def build():
    from ark import steps
    return steps.build(run_state(jobs={}))


def test_a_worker_row_takes_tokens_and_model_from_model_usage(ark_home):
    receipt("shell.worker", 0, 10, log='{"type":"system"}\n' + result_line(inp=10, out=200, cache_read=3000, cache_write=400))
    [row] = build()
    assert row["kind"] == "worker" and row["job"] == "shell" and row["attempt"] == 1
    assert row["tokens"] == {"in": 10, "out": 200, "cache_read": 3000, "cache_write": 400}
    assert row["tokens_total"] == 610                       # in + out + cache_write, as the tier caps count
    assert row["model"] == "sonnet" and row["tier"] == "T1"  # P2-era receipts have no meta: the model says the tier
    assert row["minutes"] == 10.0 and row["usd"] == 0.25 and row["outcome"] == "done"
    assert row["estimated"] == []


def test_attempts_count_in_order_and_meta_wins(ark_home):
    receipt("shell.worker", 0, 5, result="STATUS: FAILED gate", log=result_line())
    receipt("shell.worker", 6, 9, meta={"tier": "T2", "attempt": 7}, log=result_line(model="claude-opus-5-5"))
    rows = build()
    assert [(r["attempt"], r["tier"], r["model"], r["outcome"]) for r in rows] == \
        [(1, "T1", "sonnet", "failed"), (7, "T2", "opus", "done")]


def test_script_steps_have_no_tokens(ark_home):
    receipt("shell.gate", 0, 2, exit=1, result="1 failed", usd=None, log="FAILED\n")
    receipt("shell.integrate", 3, 4, result=json.dumps({"ok": True, "base": "a", "head": "b"}), usd=None, log=None)
    gate, integrate = build()
    assert (gate["kind"], gate["model"], gate["tokens"], gate["outcome"]) == ("gate", "script", None, "red")
    assert (integrate["outcome"], integrate["estimated"]) == ("green", [])  # a script costs nothing: not estimated


def test_review_verdict_and_ticks(ark_home):
    receipt("shell.review", 0, 1, result='{"verdict": "FIX", "findings": []}', log=result_line())
    receipt("_decisions.tick", 2, 3, result="# Decisions — x", log=result_line())
    review, decisions = build()
    assert review["outcome"] == "fix"
    assert (decisions["kind"], decisions["job"], decisions["outcome"]) == ("decisions", None, "ok")


def test_a_log_without_result_line_is_estimated(ark_home):
    line = {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 5, "output_tokens": 50,
                                                                   "cache_creation_input_tokens": 45}}}
    receipt("shell.worker", 0, 3, exit=-15, reason="stopped by Marco", usd=None, log=json.dumps(line) + "\n")
    [row] = build()
    assert row["tokens"] is None and row["tokens_total"] == 100
    assert row["outcome"] == "stopped" and row["reason"] == "stopped by Marco"
    assert set(row["estimated"]) == {"tokens", "usd", "model"}


def test_a_planted_link_is_not_followed(ark_home, tmp_path):
    secret = tmp_path / "secret.log"
    secret.write_text(result_line(out=999999))
    log = receipt("shell.worker", 0, 3, log=None)
    log.symlink_to(secret)
    [row] = build()
    assert row["tokens"] is None and not row["tokens_total"]


def test_unknown_names_and_broken_receipts_are_skipped(ark_home):
    receipt("shell.worker", 0, 3, log=result_line())
    receipt("weird", 0, 3, log=None)
    (done_dir() / "broken-1.json").write_text("{not json")
    assert [r["kind"] for r in build()] == ["worker"]


def test_merges_come_from_events_and_a_lost_event_is_estimated(ark_home):
    from ark import steps
    st = run_state(events=[f"{iso(20)} · shell: merged into ark/notes-edit", f"{iso(21)} · something else"])
    rows = steps.build(st)
    by_job = {r["job"]: r for r in rows if r["kind"] == "merge"}
    assert by_job["shell"]["start"] == iso(20) and by_job["shell"]["estimated"] == []
    assert by_job["ui"]["start"] is None and by_job["ui"]["estimated"] == ["time"]   # merged, event gone
    assert rows[-1]["job"] == "ui"                                                  # no time: last


def test_a_blocked_job_has_no_merge_row(ark_home):
    from ark import steps
    rows = steps.build(run_state(jobs={"shell": job("blocked")}))
    assert [r for r in rows if r["kind"] == "merge"] == []


def test_waits_use_asked_or_the_event(ark_home):
    from ark import steps
    st = run_state(
        events=[f"{iso(30)} · ALERT question q2 from ui: which store? — `ark answer`"],
        answers=[{"id": "q1", "kind": "worker", "job": "shell", "question": "a?", "answer": "b", "asked": iso(10), "at": iso(25)},
                 {"id": "q2", "kind": "worker", "job": "ui", "question": "c?", "answer": "d", "at": iso(40)}],
        questions=[{"id": "q3", "kind": "cap_run", "job": None, "text": "raise?", "asked": iso(50)}])
    waits = [r for r in steps.build(st) if r["kind"] == "wait-marco"]
    assert [(w["job"], w["minutes"], w["outcome"]) for w in waits] == \
        [("shell", 15.0, "answered"), ("ui", 10.0, "answered"), (None, None, "open")]
    assert waits[0]["reason"] == "worker"


def test_write_numbers_steps_in_time_order(ark_home):
    from ark import paths, steps
    receipt("ui.worker", 5, 9, log=result_line())
    receipt("shell.worker", 0, 4, log=result_line())
    st = run_state(jobs={"shell": job("blocked"), "ui": job("blocked")})
    path = steps.write(st, steps.build(st))
    assert path == paths.run_dir("demo", "notes-edit") / "steps.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(r["step"], r["job"]) for r in rows] == [(1, "shell"), (2, "ui")]


def test_resolve_keeps_when_it_was_asked():
    from ark import questions
    st = {"repo": "demo", "slug": "notes-edit", "events": [], "alerts": [], "questions": []}
    q = questions.ask(st, "worker", "which store?", job="shell")
    answer = questions.resolve(st, q["id"], "sqlite")
    assert answer["asked"] == q["asked"]
