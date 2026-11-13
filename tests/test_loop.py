import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import approve, job, manifest, plan_text

from ark import claude, forge, gitops, integrate, loop, merge, paths, proc, run
from ark import euklides as euklides_mod, inbox, questions
from ark.plan import sha256_file

SLUG = "notes-edit"


@pytest.fixture
def feature(runner, make_feature):
    """Seed a repo, write the plan on the desk and approve it."""
    def make(m=None, repo="demo", slug=SLUG, tasks=("T1", "T2", "T3")):
        r = runner(repo)
        p = make_feature(plan_text(m, tasks=tasks, repo=repo, slug=slug), repo=repo, slug=slug, create_repo=False)
        approve(p, repo, slug)
        return r, p
    return make


def state(repo="demo", slug=SLUG):
    return run.load(repo, slug)


def jobs_settled(repo="demo", slug=SLUG):
    st = state(repo, slug)
    return bool(st and st["jobs"]) and all(j["status"] in run.TERMINAL for j in st["jobs"].values()) \
        and not proc.active(paths.run_dir(repo, slug))


def drive(until, cycles=400):
    for _ in range(cycles):
        loop.cycle()
        if until():
            return
        time.sleep(0.05)
    raise AssertionError("not reached:\n" + "\n".join(
        f.read_text() for f in paths.runs_dir().glob("**/*.md")))


def stopping(until, repo="demo", slug=SLUG):
    """`until`, while Marco answers :stop to every question Euklides asks him: a job Euklides cannot
    fix ends blocked, as one did before Euklides."""
    sent = set()

    def check():
        for q in (state(repo, slug) or {}).get("questions") or []:
            if q["kind"] == "worker" and q["id"] not in sent:
                sent.add(q["id"])
                inbox.post(repo, slug, "answer", id=q["id"], text=":stop")
        return until()
    return check


def asked_marco(text, repo="demo", slug=SLUG):
    """Whether a question Marco answered held `text`."""
    return any(text in a["question"] for a in (state(repo, slug) or {}).get("answers") or [])


def test_jobs_merge_in_order(feature):
    repo, _ = feature()
    drive(jobs_settled)
    st = state()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"]
    gitops.fetch(repo)
    files = gitops.changed_files(repo, st["base_sha"], f"origin/ark/{SLUG}")
    assert files == [f"docs/decisions/{SLUG}.md", "src/shell/shell.txt", "src/ui/ui.txt"]
    prs = forge.LocalForge().list(repo)
    assert [(p["base"], p["head"], p["state"]) for p in prs] == [
        (f"ark/{SLUG}", f"ark/{SLUG}-shell", "MERGED"), (f"ark/{SLUG}", f"ark/{SLUG}-ui", "MERGED"),
        ("master", f"ark/{SLUG}", "OPEN")]
    log = gitops.git(repo, "log", "--format=%s", f"origin/ark/{SLUG}")
    assert log.splitlines()[:3] == [f"docs: decisions for {SLUG}", "feat: ui", "feat: shell"]
    assert gitops.git(repo, "rev-parse", "origin/master") == st["base_sha"]     # default branch untouched


def waits_for_marco(job="shell"):
    st = state()
    return bool(st and st["jobs"]) and st["jobs"][job]["status"] == "waiting_answer"


def answer_open_question(text, job="shell"):
    [q] = [q for q in state()["questions"] if q["job"] == job]
    inbox.post("demo", SLUG, "answer", id=q["id"], text=text)
    return q


def test_outside_area_blocks_the_job_and_its_dependents(feature, monkeypatch):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    repo, _ = feature()
    drive(waits_for_marco)
    q = answer_open_question("ok")
    assert "outside its area" in q["text"] and "Proposal: :stop" in q["text"]
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and st["jobs"]["ui"]["status"] == "blocked"
    assert "depends on is blocked: shell" in st["jobs"]["ui"]["reason"] and st["jobs"]["ui"]["waiting_on"] == "shell"
    assert gitops.remote_sha(repo, f"ark/{SLUG}-shell") is None                 # never pushed


def test_a_failed_attempt_goes_to_euklides_and_is_retried(feature, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_FAIL", "shell:1")
    repo, _ = feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 2 and j["tier_path"] == ["T1"]
    assert any("euklides: retry" in e for e in state()["events"])
    assert (tmp_path / "fake" / "euklides.count").read_text() == "1"
    env = json.loads((tmp_path / "fake" / "euklides.1.env").read_text())  # Euklides has Bash: the push layer too
    assert env["ARK_ROLE"] == "euklides"
    n = int(env["GIT_CONFIG_COUNT"]) - 1  # the last entry: the one runner_env added
    assert env[f"GIT_CONFIG_KEY_{n}"] == f"url.{claude.DEAD_PUSH_URL}.pushInsteadOf"
    assert env[f"GIT_CONFIG_VALUE_{n}"] == gitops.origin_url(repo)
    prompt = (tmp_path / "fake" / "euklides.1.prompt").read_text()
    assert f"## The branch against the run's base (origin/ark/{SLUG})" in prompt and "(no commits)" in prompt


def test_review_fix_resumes_the_worker(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FIX", "shell")
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["review_rounds"] == 1 and j["attempts"] == 2


def test_red_integration_gate_never_merges(feature):
    repo, _ = feature(manifest(integration_gate="exit 1"))
    drive(waits_for_marco)
    q = answer_open_question(":stop")
    assert "red on" in q["text"]
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked"
    assert st["jobs"]["shell"]["attempts"] == 1                                 # red on the base too: no second attempt
    gitops.fetch(repo)
    assert gitops.changed_files(repo, st["base_sha"], f"origin/ark/{SLUG}") == [f"docs/decisions/{SLUG}.md"]  # nothing merged


def test_an_integration_gate_failed_twice_is_unblocked_and_runs_again(feature, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "unblock", "note": "merge the base"}')
    count = tmp_path / "fake" / "igate"
    gate = f'n=$(cat "{count}" 2>/dev/null || echo 0); echo $((n+1)) > "{count}"; [ "$n" -ge 2 ]'
    feature(manifest(integration_gate=gate))
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["integrate_fails"] == 0
    assert [r["action"] for r in euklides_mod.decisions()] == ["unblock"]


def test_existing_integration_branch_keeps_the_run_waiting(feature):
    repo, _ = feature()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), f"ark/{SLUG}")
    loop.cycle()
    st = state()
    assert st["status"] == "waiting" and "already exists" in st["reason"]
    assert not proc.active(paths.run_dir("demo", SLUG))


def test_a_state_saved_while_judging_is_loaded_as_a_euklides_step(feature):
    feature()
    loop.cycle()
    st = state()
    st["jobs"]["shell"].update(status="needs_judge", failure="tests are red")
    st["jobs"]["ui"].update(status="judging", question="which store?")
    run.save(st)
    got = loop._load("demo", SLUG)["jobs"]
    assert got["shell"]["status"] == "needs_euklides"
    assert got["shell"]["euklides"] == {"trigger": "failure", "situation": "tests are red", "kind": None}
    assert got["ui"]["status"] == "needs_euklides" and got["ui"]["euklides"]["trigger"] == "question"


def test_euklides_is_a_model_step():
    assert loop.RUNNING["euklides"] == "euklides" and loop.WAITING["euklides"] == "needs_euklides"
    assert "euklides" in loop.MODEL_KINDS and loop.HANDLERS["euklides"] is loop.on_euklides


# ---- beyond the plan's list: each of these is a way an unattended loop could go wrong ------------

def bare(tmp_path, status="working", **over):
    """A context with no repo behind it, for handlers that decide from a receipt alone."""
    st = run.new_state({"repo": "demo", "slug": SLUG, "plan": "/nowhere", "sha256": "0" * 64, "approved_at": "t"})
    mj = job("shell", ["T1"], ["src/shell/**"])
    if status in ("euklides", "needs_euklides"):
        over.setdefault("euklides", {"trigger": "failure", "situation": "red", "kind": None})
    st["jobs"]["shell"] = {**run.new_job(mj), "status": status, **over}
    return SimpleNamespace(st=st, dir=tmp_path, base=f"ark/{SLUG}", mjobs={"shell": mj}, repo=tmp_path,
                           wt=lambda j: tmp_path / j, branch=lambda j: f"ark/{SLUG}-{j}")


def receipt(result="", exit=0, reason="", **over):
    return {"exit": exit, "reason": reason, "result": result, "usd": None, "session": None, "is_error": False,
            "meta": {"head": "abc"}, **over}


def consume_one(monkeypatch, c, name, r):
    monkeypatch.setattr(proc, "reap", lambda d: [])
    monkeypatch.setattr(proc, "finished", lambda d: [name])
    monkeypatch.setattr(proc, "consume", lambda d, n: r)
    loop.consume_receipts(c)


def limits(ark_home, **values):
    f = ark_home / "workspace.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "limits": values}))


def is_gone(pid):
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


# -- a damaged run state

@pytest.mark.parametrize("damage", ["{not json", "[]", '{"repo": "demo"}', "\xff"])
def test_a_damaged_state_alerts_once_is_never_replaced_and_stops_no_other_run(feature, damage):
    feature()
    feature(repo="other", slug="fine")
    f = run.state_file("demo", SLUG)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(damage.encode("latin-1"))
    before = f.read_bytes()
    seen = [a for _ in range(3) for a in loop.cycle()[1] if a.startswith(f"demo/{SLUG}:")]
    assert len(seen) == 1 and "skipped" in seen[0]
    assert f.read_bytes() == before
    assert not proc.active(paths.run_dir("demo", SLUG))
    drive(lambda: jobs_settled("other", "fine"))
    assert state("other", "fine")["jobs"]["shell"]["status"] == "merged"


def test_a_job_of_the_wrong_shape_is_damage(feature):
    feature()
    loop.cycle()
    st = state()
    st["jobs"]["shell"] = "working"
    run.state_file("demo", SLUG).write_text(json.dumps(st))
    assert any("skipped" in a for a in loop.cycle()[1])
    good = {**st, "jobs": {**st["jobs"], "shell": {**run.new_job(job("shell", [], [])), "status": "working"}}}
    run.state_file("demo", SLUG).write_text(json.dumps(good))
    drive(jobs_settled)                                                         # readable again: it goes on
    assert state()["jobs"]["shell"]["status"] == "merged"


# -- leftovers in a worktree

def test_a_retry_waits_for_what_is_still_in_the_worktree(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1")
    feature()
    holders = [4242]
    monkeypatch.setattr(proc, "cwd_holders", lambda p: holders if Path(p).exists() else [])
    drive(lambda: "holder_tries" in state()["jobs"]["shell"])
    for _ in range(3):
        loop.cycle()
    st = state()
    j = st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and j["attempts"] == 1 and j["holder_tries"] == 1
    assert not [e for e in st["events"] if "ALERT" in e]                        # it heals by waiting: Marco hears nothing
    assert len([e for e in st["events"] if "4242" in e]) == 1
    holders.clear()
    st["jobs"]["shell"].pop("not_before")
    run.save(st)
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 2 and "holder_tries" not in j


@pytest.mark.parametrize("holders", [lambda p: [4242], "error"])
def test_holders_that_persist_go_to_euklides_and_not_knowing_counts_as_holders(feature, monkeypatch, holders):
    feature()
    def fails(p):
        raise proc.CwdHoldersError("lsof failed")
    monkeypatch.setattr(proc, "cwd_holders", fails if holders == "error" else holders)
    alerts = loop.cycle()[1] + loop.cycle()[1]
    assert state()["jobs"]["shell"]["status"] == "pending"
    assert not proc.active(paths.run_dir("demo", SLUG))
    assert not [a for a in alerts if "shell waits" in a]
    for _ in range(10):
        st = state()
        st["jobs"]["shell"].pop("not_before", None)
        run.save(st)
        loop.cycle()
        if state()["jobs"]["shell"]["status"] == "needs_euklides":
            break
    j = state()["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and "worktree" in j["euklides"]["situation"]
    assert j["euklides"]["back"] == "pending"


def test_a_held_job_gives_its_writer_slot_to_the_next(feature, monkeypatch, ark_home):
    limits(ark_home, writers_per_repo=1)
    feature(manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]), job("ui", ["T3"], ["src/ui/**"])]))
    monkeypatch.setattr(proc, "cwd_holders", lambda p: [4242] if Path(p).name == "shell" else [])
    loop.cycle()
    st = state()
    assert st["jobs"]["shell"]["status"] == "pending" and st["jobs"]["ui"]["status"] == "working"
    monkeypatch.setattr(proc, "cwd_holders", lambda p: [])
    st["jobs"]["shell"].pop("not_before", None)
    run.save(st)
    drive(jobs_settled)


def test_the_writer_limit_holds_across_jobs(feature, ark_home, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "0.3")  # or the workers never overlap, limit or no limit
    limits(ark_home, writers_per_repo=1)
    feature(manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]), job("ui", ["T3"], ["src/ui/**"])]))
    drive(jobs_settled)
    assert (tmp_path / "fake" / "max_active").read_text() == "1"


def test_survivors_reach_marco(tmp_path, monkeypatch):
    c = bare(tmp_path)
    consume_one(monkeypatch, c, "shell.worker", receipt(exit=-15, reason="timeout: over its time limit", survivors=True))
    assert len(c.st["alerts"]) == 1 and "outlived" in c.st["alerts"][0] and "timeout" in c.st["alerts"][0]
    assert c.st["jobs"]["shell"]["status"] == "needs_euklides"


# -- receipts

@pytest.mark.parametrize("status,kind", [("gating", "gate"), ("reviewing", "review"), ("integrating", "integrate"),
                                         ("euklides", "euklides"), ("working", "worker")])
def test_stopped_by_marco_blocks_whatever_was_running(tmp_path, monkeypatch, status, kind):
    c = bare(tmp_path, status=status)
    consume_one(monkeypatch, c, f"shell.{kind}", receipt(exit=-15, reason="stopped by Marco"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "blocked" and j["reason"] == "stopped by Marco" and j["tier_attempts"] == 0


def test_a_receipt_nothing_waits_for_is_ignored(tmp_path, monkeypatch):
    c = bare(tmp_path, status="needs_review")
    consume_one(monkeypatch, c, "shell.gate", receipt(exit=1))
    assert c.st["jobs"]["shell"]["status"] == "needs_review"
    assert any("ignored" in e for e in c.st["events"])


@pytest.mark.parametrize("usd,total", [(0.5, 0.5), (float("nan"), 0), ("3", 0), (True, 0)])
def test_only_a_real_cost_is_added(tmp_path, monkeypatch, usd, total):
    c = bare(tmp_path)
    consume_one(monkeypatch, c, "shell.worker", receipt(reason="x", usd=usd))
    assert c.st["jobs"]["shell"]["usd"] == total


def test_a_handler_that_raises_fails_the_job_instead_of_losing_it(tmp_path, monkeypatch):
    c = bare(tmp_path, status="gating")
    monkeypatch.setitem(loop.HANDLERS, "gate", lambda *a: 1 / 0)
    consume_one(monkeypatch, c, "shell.gate", receipt())
    assert c.st["jobs"]["shell"]["status"] == "needs_euklides" and "ZeroDivisionError" in c.st["jobs"]["shell"]["failure"]


# -- the worker's receipt

@pytest.mark.parametrize("r", [receipt("STATUS: DONE", exit=1), receipt("STATUS: DONE", is_error=True)])
def test_a_worker_that_ended_in_error_is_a_failure_whatever_it_says(tmp_path, r):
    c = bare(tmp_path)
    loop.on_worker(c, "shell", r)
    assert c.st["jobs"]["shell"]["status"] == "needs_euklides" and "error" in c.st["jobs"]["shell"]["failure"]


@pytest.mark.parametrize("r,done", [(receipt("STATUS: FAILED — no"), True), (receipt(reason="setup failed: x", exit=1), False),
                                    (receipt(reason="timeout: over its time limit", exit=-15), False)])
def test_setup_counts_as_done_only_when_the_process_got_through_it(tmp_path, r, done):
    c = bare(tmp_path)
    loop.on_worker(c, "shell", r)
    assert c.st["jobs"]["shell"]["setup_done"] is done


def test_a_file_name_that_is_not_utf8_cannot_break_the_state(tmp_path, monkeypatch):
    c = bare(tmp_path)
    monkeypatch.setattr(gitops, "is_clean", lambda wt: True)
    monkeypatch.setattr(gitops, "current_branch", lambda wt: f"ark/{SLUG}-shell")
    monkeypatch.setattr(gitops, "changed_files", lambda *a: ["bad\udcff.txt"])
    loop.on_worker(c, "shell", receipt("STATUS: DONE"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and "outside its area" in j["euklides"]["situation"]
    run.render(c.st).encode("utf-8")


# -- Euklides' limits

def test_a_retry_past_the_budget_is_not_open_and_asks_marco(tmp_path, ark_home):
    c = bare(tmp_path, status="needs_euklides", usd=5.0, euklides={"trigger": "failure", "situation": "red", "kind": None})
    assert euklides_mod.allowed("failure", c.st["jobs"]["shell"], 2) == ["fixed", "ask"]  # fixed pays no worker


def test_retries_are_capped_by_euklides_decisions_not_by_the_tier(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1,shell:2")
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["tier_path"] == ["T1"] and j["attempts"] == 3


def test_the_fifth_failure_asks_marco_and_stop_blocks_the_dependents(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1,shell:2,shell:3,shell:4,shell:5")
    feature()
    drive(waits_for_marco)
    assert state()["jobs"]["shell"]["euklides_count"] == 4
    answer_open_question(":stop")
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and st["jobs"]["ui"]["status"] == "blocked"


# -- the reviewer

@pytest.mark.parametrize("r", [
    receipt("looks fine"), receipt('{"verdict": NaN, "findings": []}'), receipt('{"verdict": ["CLEAN"], "findings": []}'),
    receipt('{"verdict": "CLEAN"}'), receipt('{"verdict": "CLEAN", "findings": "none"}'),
    receipt('{"verdict": "CLEAN", "findings": [], "extra": 1}'), receipt('{"verdict": "FIX", "findings": []}'),
    receipt('{"verdict": "CLEAN", "findings": []}', exit=1), receipt('{"verdict": "CLEAN", "findings": []}', is_error=True),
])
def test_an_unusable_review_is_no_verdict(tmp_path, r):
    c = bare(tmp_path, status="reviewing")
    loop.on_review(c, "shell", r)
    assert c.st["jobs"]["shell"]["status"] == "needs_review"
    assert merge.read_record(tmp_path, "shell", "verdict") is None
    loop.on_review(c, "shell", r)
    assert c.st["jobs"]["shell"]["status"] == "needs_euklides"


def test_a_real_verdict_forgives_an_earlier_review_error(tmp_path):
    c = bare(tmp_path, status="reviewing", review_errors=1)
    loop.on_review(c, "shell", receipt('{"verdict": "CLEAN", "findings": []}'))
    assert c.st["jobs"]["shell"]["review_errors"] == 0 and c.st["jobs"]["shell"]["status"] == "needs_integrate"


# -- the integration gate's receipt

@pytest.mark.parametrize("r", [receipt(exit=-15, reason="timeout: over its time limit"), receipt("", exit=-9),
                               receipt("Traceback, no JSON", exit=1)])
def test_a_check_with_no_result_is_not_a_red_gate(tmp_path, r):
    c = bare(tmp_path, status="integrating")
    loop.on_integrate(c, "shell", r)
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_integrate" and j["integrate_fails"] == 0 and not j["resume_msg"]
    assert merge.read_record(tmp_path, "shell", "integrate") is None
    for _ in range(4):
        j.pop("not_before", None)
        j["status"] = "integrating"
        loop.on_integrate(c, "shell", r)
        assert j["status"] == "needs_integrate"
    j["status"] = "integrating"
    loop.on_integrate(c, "shell", r)
    assert j["status"] == "needs_euklides" and j["euklides"]["back"] == "needs_integrate" and not c.st["alerts"]


SKIPPED = json.dumps({"ok": False, "base": None, "head": "abc", "reason": "the plan changed after its approval", "tail": ""})


def test_a_check_that_could_not_run_is_not_charged_to_the_worker(tmp_path):
    c = bare(tmp_path, status="integrating")
    for _ in range(5):
        loop.on_integrate(c, "shell", receipt(SKIPPED, exit=1))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_integrate" and j["integrate_fails"] == 0 and j["attempts"] == 0
    assert merge.read_record(tmp_path, "shell", "integrate") is None
    assert c.st["alerts"] == []


def test_a_check_that_could_not_run_gives_its_heavy_slot_to_the_next_job(tmp_path):
    c = bare(tmp_path, status="integrating", heavy=True)
    loop.on_integrate(c, "shell", receipt(SKIPPED, exit=1))
    j = c.st["jobs"]["shell"]
    assert j["not_before"] > time.time()
    slots = loop.Slots([c.st], {"writers_per_repo": 1, "writers_total": 1, "heavy_gates": 1})
    assert loop._next_step(c, "shell", j, slots) is None and slots.heavy == 0


def test_a_check_that_never_runs_goes_to_euklides(tmp_path):
    c = bare(tmp_path, status="integrating")
    for _ in range(5):
        loop.on_integrate(c, "shell", receipt(SKIPPED, exit=1))
    assert c.st["jobs"]["shell"]["status"] == "needs_integrate"
    loop.on_integrate(c, "shell", receipt(SKIPPED, exit=1))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and "could not run" in j["euklides"]["situation"]
    assert j["euklides"]["back"] == "needs_integrate"


def test_a_check_that_ran_ends_the_waiting(tmp_path):
    c = bare(tmp_path, status="integrating", integrate_skips=3, not_before=time.time() + 60)
    loop.on_integrate(c, "shell", receipt(json.dumps({"ok": True, "base": "b" * 40, "head": "abc", "reason": "", "tail": ""})))
    assert "not_before" not in c.st["jobs"]["shell"] and "integrate_skips" not in c.st["jobs"]["shell"]


@pytest.mark.parametrize("ok", [False, 1, "true"])
def test_only_a_true_ok_is_green(tmp_path, ok):
    c = bare(tmp_path, status="integrating")
    loop.on_integrate(c, "shell", receipt(json.dumps({"ok": ok, "base": "b" * 40, "head": "abc", "reason": "red", "tail": "t"})))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and j["integrate_fails"] == 1 and "red" in j["resume_msg"]
    assert merge.read_record(tmp_path, "shell", "integrate")["ok"] is False


def test_a_red_result_clears_the_counts_of_lost_checks(tmp_path):
    c = bare(tmp_path, status="integrating", integrate_lost=1, integrate_skips=3)
    loop.on_integrate(c, "shell", receipt(json.dumps({"ok": True, "base": "b" * 40, "head": "abc", "reason": "", "tail": ""})))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_merge" and "integrate_lost" not in j and "integrate_skips" not in j


def test_a_red_gate_full_of_non_ascii_still_reaches_the_loop_as_red(feature):
    gate = f"{sys.executable} -c \"import sys; sys.stdout.buffer.write(b'\\xc3\\xa9' * 3000)\"; exit 1"
    feature(manifest(integration_gate=gate))
    drive(stopping(jobs_settled))
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and asked_marco("(exit 1)")  # red on the base too: Euklides at once
    assert "é" in merge.read_record(paths.run_dir("demo", SLUG), "shell", "integrate")["tail"]


def test_the_json_line_of_a_check_always_fits(capsys):
    out = {"ok": False, "base": "b" * 40, "head": "h" * 40, "reason": "é" * 5000, "tail": "é" * integrate.TAIL}
    d = json.loads(integrate._json_line(out))
    assert len(integrate._json_line(out)) <= integrate.MAX_LINE and d["reason"] == "é" * integrate.REASON
    assert d["tail"] and out["tail"].endswith(d["tail"])
    assert json.loads(integrate._json_line({**out, "reason": "r", "tail": "plain"}))["tail"] == "plain"


def test_stopping_ark_integrate_under_proc_kills_the_gate_and_leaves_no_scratch(feature, tmp_path):
    marker = tmp_path / "gate.pid"
    feature(manifest(integration_gate=f"echo $$ > {marker}; sleep 60 & wait"))
    drive(marker.exists)
    pid = int(marker.read_text())
    assert proc.stop(paths.run_dir("demo", SLUG), "shell.integrate", "stopped by Marco")
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and st["jobs"]["shell"]["reason"] == "stopped by Marco"
    assert is_gone(pid)
    assert not (paths.worktrees_dir() / "demo" / SLUG / "_integrate-shell").exists()


# -- what the processes are started with

def test_every_process_gets_the_push_layer_and_the_check_outlives_its_own_timeout(feature, monkeypatch):
    repo, _ = feature()
    started, real = {}, proc.start
    def spy(run_dir, name, cmd, **kw):
        started[name] = kw
        return real(run_dir, name, cmd, **kw)
    monkeypatch.setattr(proc, "start", spy)
    drive(jobs_settled)
    url = gitops.origin_url(repo)
    for kind in ("worker", "gate", "review", "integrate"):
        env = started[f"shell.{kind}"]["env"]
        n = int(env["GIT_CONFIG_COUNT"]) - 1
        assert env[f"GIT_CONFIG_VALUE_{n}"] == url, kind
    assert started["shell.integrate"]["minutes"] > integrate.GATE_TIMEOUT / 60


# -- merging

def test_a_merge_whose_result_was_never_saved_is_confirmed_before_anything_else(feature):
    feature()
    drive(jobs_settled)
    st = state()
    st["jobs"]["shell"]["status"] = "needs_merge"                               # the base has moved since (ui merged)
    c = loop.Ctx(st, forge.LocalForge())
    loop.do_merge(c, "shell")
    assert st["jobs"]["shell"]["status"] == "merged"


def test_a_worktree_that_will_not_go_does_not_unmerge_a_job(feature, monkeypatch):
    feature()
    real = gitops.remove_worktree
    def stuck(repo, path):                                                      # the forge removes its scratch here too
        if path.name in ("shell", "ui"):
            raise gitops.GitError("busy")
        return real(repo, path)
    monkeypatch.setattr(gitops, "remove_worktree", stuck)
    drive(jobs_settled)
    st = state()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"]
    assert any("its worktree stays" in e for e in st["events"])


# -- errors while moving a job

@pytest.mark.parametrize("e", [gitops.GitError("offline"), forge.ForgeError("offline"),
                               ValueError("embedded null byte"), OSError("is a directory")])
def test_an_error_on_a_waiting_job_retries_then_goes_to_euklides_without_an_alert(tmp_path, e):
    c = bare(tmp_path, status="needs_merge")
    for _ in range(6):
        c.st["jobs"]["shell"].pop("not_before", None)
        loop._error(c, "shell", e)
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and str(e) in j["euklides"]["situation"]
    assert j["euklides"]["back"] == "needs_merge" and c.st["alerts"] == []


def test_a_running_job_keeps_its_errors_and_alerts_once(tmp_path):
    c = bare(tmp_path, status="working")
    for _ in range(loop.ERROR_ALERT_AFTER * 2):
        loop._error(c, "shell", gitops.GitError("offline"))
    assert c.st["jobs"]["shell"]["status"] == "working" and c.st["jobs"]["shell"]["errors"] == loop.ERROR_ALERT_AFTER * 2
    assert len(c.st["alerts"]) == 1 and "offline" in c.st["alerts"][0]


def test_a_step_that_works_clears_the_error_and_one_job_does_not_stop_the_next(tmp_path, monkeypatch):
    c = bare(tmp_path, status="needs_merge")
    c.st["jobs"]["ui"] = {**run.new_job(job("ui", [], [])), "status": "needs_merge"}
    c.mjobs["ui"] = job("ui", [], [])
    seen = []
    def merge_it(c, j):
        seen.append(j)
        if j == "shell" and seen.count("shell") == 1:
            raise forge.ForgeError("gh is down")
    monkeypatch.setattr(loop, "do_merge", merge_it)
    slots = loop.Slots([c.st], {"writers_per_repo": 1, "writers_total": 1, "heavy_gates": 1})
    loop.schedule(c, slots)
    assert seen == ["shell", "ui"] and c.st["jobs"]["shell"]["step_tries"] == 1
    c.st["jobs"]["shell"].pop("not_before")
    loop.schedule(c, slots)
    assert "step_tries" not in c.st["jobs"]["shell"] and "error" not in c.st["jobs"]["shell"]


# -- crashes between a process and the saved state

@pytest.mark.parametrize("status,kind,after", [("working", "worker", "needs_worker"), ("gating", "gate", "needs_gate"),
                                               ("reviewing", "review", "needs_review"),
                                               ("integrating", "integrate", "needs_integrate"),
                                               ("euklides", "euklides", "needs_euklides")])
def test_a_process_gone_without_a_result_goes_back_a_step(ark_home, status, kind, after, tmp_path):
    st = bare(tmp_path, status=status).st
    loop.reconcile(st)
    assert st["jobs"]["shell"]["status"] == after
    assert loop.RUNNING[status] == kind


@pytest.mark.parametrize("status,kind,after,attempts", [
    ("pending", "worker", "working", 1), ("needs_worker", "worker", "working", 1), ("needs_gate", "gate", "gating", 0),
    ("needs_review", "review", "reviewing", 0), ("needs_integrate", "integrate", "integrating", 0),
    ("needs_euklides", "euklides", "euklides", 0)])
def test_a_process_the_state_missed_is_adopted_never_started_twice(ark_home, status, kind, after, attempts, tmp_path):
    st = bare(tmp_path, status=status).st
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    (d / f"shell.{kind}.proc").write_text("{}")
    loop.reconcile(st)
    assert st["jobs"]["shell"]["status"] == after and st["jobs"]["shell"]["attempts"] == attempts


@pytest.mark.parametrize("stale", [{}, {"soft_deadline": 1, "watch_from": 1, "token_factor": 1.5, "waited": True,
                                       "anomaly": "over its time for tier T1"}])
def test_an_adopted_worker_is_watched_from_when_it_started(ark_home, stale, tmp_path):
    st = bare(tmp_path, status="needs_worker").st
    j = st["jobs"]["shell"]
    j.update(stale)
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    start = time.time() - 300
    (d / "shell.worker.proc").write_text(json.dumps({"name": "shell.worker", "pid": 2, "leader_cmd": "x", "deadline": start + 7200,
                                                   "leader_start": start, "start": start}))
    loop.reconcile(st)
    assert j["status"] == "working"
    assert j["soft_deadline"] == start + claude.TIER_MINUTES[j["tier"]] * 60
    assert j["watch_from"] == start
    assert (j["token_factor"], j["waited"], j["anomaly"]) == (1, False, None)


def test_an_adopted_worker_with_an_unreadable_record_is_watched_from_now(ark_home, tmp_path):
    st = bare(tmp_path, status="needs_worker").st
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    (d / "shell.worker.proc").write_text("not json")
    before = time.time()
    loop.reconcile(st)
    assert st["jobs"]["shell"]["status"] == "working" and st["jobs"]["shell"]["watch_from"] >= before


@pytest.mark.parametrize("status", ["merged", "blocked", "needs_push", "needs_pr", "needs_merge"])
def test_a_job_with_nothing_to_run_is_left_alone(ark_home, status, tmp_path):
    st = bare(tmp_path, status=status).st
    loop.reconcile(st)
    assert st["jobs"]["shell"]["status"] == status and not st["events"]


def test_a_crash_after_the_first_push_does_not_leave_the_run_waiting_forever(feature, monkeypatch):
    repo, _ = feature()
    real = gitops.push_ref
    def dies_after(*a):
        real(*a)
        raise gitops.GitError("the laptop went to sleep")
    monkeypatch.setattr(gitops, "push_ref", dies_after)
    assert any("runner error" in a for a in loop.cycle()[1])
    st = state()
    assert st["status"] == "waiting" and st["base_sha"] == gitops.remote_sha(repo, f"ark/{SLUG}")
    monkeypatch.setattr(gitops, "push_ref", real)
    drive(jobs_settled)
    assert state()["status"] == "done" and state()["jobs"]["shell"]["status"] == "merged"


# -- the plan under a run

def test_a_changed_plan_holds_the_run_and_its_return_resumes_it(feature):
    _, p = feature()
    loop.cycle()
    good = p.read_bytes()
    p.write_bytes(good + b"\nsomeone edited this\n")
    alerts = loop.cycle()[1] + loop.cycle()[1]
    assert state()["status"] == "held" and len([a for a in alerts if "plan changed" in a]) == 1
    for _ in range(40):
        loop.cycle()
        if proc.finished(paths.run_dir("demo", SLUG)):
            break
        time.sleep(0.1)
    assert state()["jobs"]["shell"]["status"] == "working"                      # its result waits
    assert state()["jobs"]["shell"]["head"] is None
    p.write_bytes(good)
    drive(jobs_settled)
    assert state()["status"] == "done" and state()["jobs"]["shell"]["status"] == "merged"


def test_a_waiting_run_takes_a_newer_approval(feature):
    repo, p = feature()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), f"ark/{SLUG}")
    loop.cycle()
    first = state()["plan_sha"]
    p.write_text(plan_text(manifest(integration_gate="true # new"), tasks=("T1", "T2", "T3")))
    approve(p, "demo", SLUG, at="2026-11-05T11:00:00+00:00")
    loop.cycle()
    assert state()["plan_sha"] != first and state()["approved_at"].startswith("2026-11-05T11")
    gitops.git(repo, "push", "-q", "origin", "--delete", f"ark/{SLUG}")
    drive(jobs_settled)
    assert state()["jobs"]["shell"]["status"] == "merged"


def test_a_repo_that_went_away_alerts_once(feature, monkeypatch):
    feature()
    loop.cycle()
    with monkeypatch.context() as m:  # never monkeypatch.undo(): it also reverts the fixtures' ARK_HOME
        m.setattr(paths, "find_repo", lambda name: None)
        alerts = loop.cycle()[1] + loop.cycle()[1]
    assert len([a for a in alerts if "not found" in a]) == 1
    drive(jobs_settled)


# -- a model that cannot be reached

def down(**over):
    now = time.time()
    return receipt("Could not reach the model",
                   **{"exit": 1, "is_error": None, "spoke": False, "start": now - 1, "end": now, **over})


@pytest.mark.parametrize("r", [down(), down(is_error=True, usd=0), down(is_error=True), down(exit=-9)])
@pytest.mark.parametrize("status,kind,back", [("working", "worker", "needs_worker"), ("reviewing", "review", "needs_review"),
                                              ("euklides", "euklides", "needs_euklides")])
def test_a_process_that_learned_nothing_charges_nothing(tmp_path, monkeypatch, r, status, kind, back):
    c = bare(tmp_path, status=status, attempts=1, failure="red")
    consume_one(monkeypatch, c, f"shell.{kind}", r)
    j = c.st["jobs"]["shell"]
    assert j["status"] == back and j["tier_attempts"] == 0 and j["review_errors"] == 0 and j["tier_path"] == ["T1"]
    assert j["not_before"] > time.time() and j["infra_tries"] == 1
    assert not c.st["alerts"]                                                   # one try may be a lost session: it heals alone
    j["status"] = status
    consume_one(monkeypatch, c, f"shell.{kind}", r)
    if kind == "euklides":                                                      # nobody else to send it to: Marco hears at the second
        assert len(c.st["alerts"]) == 1 and "unreachable" in c.st["alerts"][0]
    else:                                                                       # a passing fault: it heals by waiting
        assert c.st["alerts"] == [] and j["infra_tries"] == 2 and j["status"] == back


@pytest.mark.parametrize("r", [down(is_error=True, usd=0.5), down(start=time.time() - 1000), down(start=None), down(spoke=True),
                               down(reason="timeout: over its time limit"), down(reason="setup failed: x"),
                               down(exit=0)])
def test_a_process_that_did_something_is_charged_as_usual(tmp_path, monkeypatch, r):
    c = bare(tmp_path, status="working")
    consume_one(monkeypatch, c, "shell.worker", r)
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and j["tier_attempts"] == 1 and "not_before" not in j


@pytest.mark.parametrize("status,kind", [("gating", "gate"), ("integrating", "integrate")])
def test_a_gate_is_not_the_model_so_it_never_counts_as_down(tmp_path, monkeypatch, status, kind):
    c = bare(tmp_path, status=status)
    consume_one(monkeypatch, c, f"shell.{kind}", down())
    assert "infra_tries" not in c.st["jobs"]["shell"]


def test_a_job_waiting_out_a_backoff_takes_no_slot(tmp_path):
    c = bare(tmp_path, status="needs_worker", not_before=time.time() + 60)
    slots = loop.Slots([c.st], {"writers_per_repo": 1, "writers_total": 1, "heavy_gates": 1})
    assert loop._next_step(c, "shell", c.st["jobs"]["shell"], slots) is None
    assert slots.writers == {}
    c.st["jobs"]["shell"]["not_before"] = time.time() - 1
    assert loop._next_step(c, "shell", c.st["jobs"]["shell"], slots) is not None


def test_the_backoff_of_euklides_grows_and_has_a_ceiling(tmp_path, monkeypatch):
    c = bare(tmp_path, status="euklides")
    j = c.st["jobs"]["shell"]
    waits = []
    for _ in range(9):
        j["status"] = "euklides"
        consume_one(monkeypatch, c, "shell.euklides", down())
        waits.append(round(j["not_before"] - time.time()))
    assert waits == [15, 30, 60, 120, 240, 480, 900, 900, 900]
    assert len(c.st["alerts"]) == 1


def test_a_model_down_for_too_long_goes_to_euklides(tmp_path, monkeypatch):
    c = bare(tmp_path, status="working")
    j = c.st["jobs"]["shell"]
    for _ in range(6):
        assert j["status"] != "needs_euklides"
        j["status"] = "working"
        j.pop("not_before", None)
        consume_one(monkeypatch, c, "shell.worker", down())
    assert j["status"] == "needs_euklides" and "no answer from the model" in j["euklides"]["situation"]
    assert j["euklides"]["back"] == "needs_worker" and j["tier_attempts"] == 0 and c.st["alerts"] == []


def test_a_real_answer_ends_the_waiting(tmp_path, monkeypatch):
    c = bare(tmp_path, status="working", infra_tries=4, not_before=1.0)
    consume_one(monkeypatch, c, "shell.worker", receipt("STATUS: FAILED — no"))
    j = c.st["jobs"]["shell"]
    assert "infra_tries" not in j and "not_before" not in j and j["tier_attempts"] == 1


def test_model_down_blocks_nothing_and_the_run_goes_on_when_it_returns(feature, monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "PASSING_WAITS", (0,) * 20)
    monkeypatch.setenv("FAKE_DOWN", "1")
    feature()
    count = tmp_path / "fake" / "down.count"
    alerts = []
    for _ in range(200):
        alerts += loop.cycle()[1]
        if count.exists() and int(count.read_text()) >= 6:
            break
        time.sleep(0.05)
    assert int(count.read_text()) >= 6
    st = state()
    j = st["jobs"]["shell"]
    assert j["tier_attempts"] == 0 and j["tier_path"] == ["T1"] and j["status"] in ("needs_worker", "working")
    assert st["jobs"]["ui"]["status"] == "pending"
    assert not [a for a in alerts if "unreachable" in a]
    monkeypatch.delenv("FAKE_DOWN")
    drive(jobs_settled)
    st = state()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"]
    assert st["jobs"]["shell"]["attempts"] == 1 and st["jobs"]["shell"]["tier"] == "T1"


def test_a_retry_after_the_model_was_down_keeps_the_feedback(feature, monkeypatch):
    monkeypatch.setattr(loop, "PASSING_WAITS", (0,) * 5)
    monkeypatch.setenv("FAKE_FIX", "shell")
    feature()
    started, real = [], proc.start
    def spy(run_dir, name, cmd, **kw):
        if name == "shell.worker":
            started.append(cmd)
            if len(started) == 2:                                               # the one the review sent back
                kw["env"]["FAKE_DOWN"] = "1"
        return real(run_dir, name, cmd, **kw)
    monkeypatch.setattr(proc, "start", spy)
    drive(jobs_settled)
    assert len(started) == 3 and "src/x:1" in " ".join(started[2]) and "--resume" not in started[2]
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and not j["resume_msg"] and j["attempts"] == 2


# -- pushing a worker's result

def leftover(repo, branch):
    """A commit on origin that the worker's commits do not descend from."""
    tree = gitops.git(repo, "rev-parse", "HEAD^{tree}")
    sha = gitops.git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "left by an earlier run")
    gitops.push_ref(repo, sha, branch)
    return sha


def test_a_push_that_fails_for_a_moment_costs_no_worker(feature, monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "PASSING_WAITS", (0,) * 5)
    feature()
    real, failures = gitops.push_ref, [1, 2]
    def flaky(repo, sha, branch):
        if branch.endswith("-shell") and failures:
            failures.pop()
            raise gitops.GitError("network down")
        return real(repo, sha, branch)
    monkeypatch.setattr(gitops, "push_ref", flaky)
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert not failures and j["status"] == "merged" and "step_tries" not in j
    assert (tmp_path / "fake" / "shell.worker.count").read_text() == "1"
    assert j["tier_attempts"] == 0 and j["attempts"] == 1


def test_a_leftover_job_branch_retries_then_goes_to_euklides_without_spending(feature, tmp_path):
    repo, _ = feature()
    loop.cycle()
    sha = leftover(repo, f"ark/{SLUG}-shell")
    drive(lambda: "push_tries" in state()["jobs"]["shell"])
    j = state()["jobs"]["shell"]
    assert j["status"] == "needs_push" and j["not_before"] > time.time()
    for _ in range(10):
        st = state()
        st["jobs"]["shell"].pop("not_before", None)
        run.save(st)
        loop.cycle()
        if state()["jobs"]["shell"]["status"] == "needs_euklides":
            break
    j = state()["jobs"]["shell"]
    e = j["euklides"]
    assert j["status"] == "needs_euklides" and e["back"] == "needs_push"
    assert f"ark/{SLUG}-shell" in e["situation"] and "fast-forward" in e["situation"]
    assert (tmp_path / "fake" / "shell.worker.count").read_text() == "1" and j["tier_path"] == ["T1"]
    assert gitops.remote_sha(repo, f"ark/{SLUG}-shell") == sha                  # left for Euklides and Marco, not overwritten


def test_the_exact_commit_the_checks_saw_is_what_gets_pushed(feature, monkeypatch):
    repo, _ = feature()
    real, pushed = gitops.push_ref, {}
    def spy(repo, sha, branch):
        pushed[branch] = sha
        return real(repo, sha, branch)
    monkeypatch.setattr(gitops, "push_ref", spy)
    drive(jobs_settled)
    assert pushed[f"ark/{SLUG}-shell"] == state()["jobs"]["shell"]["head"]


@pytest.mark.parametrize("remote,ancestor", [("gone", None), (None, None), ("older", True)])
def test_a_push_error_that_is_not_a_leftover_retries(tmp_path, monkeypatch, remote, ancestor):
    c = bare(tmp_path, status="needs_push", head="a" * 40)
    def no(*a):
        raise gitops.GitError("network down")
    monkeypatch.setattr(gitops, "push_ref", no)
    monkeypatch.setattr(gitops, "remote_sha", no if remote == "gone" else lambda *a: remote)
    monkeypatch.setattr(gitops, "is_ancestor", lambda *a: ancestor)
    loop.schedule(c, loop.Slots([c.st], {"writers_per_repo": 1, "writers_total": 1, "heavy_gates": 1}))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_push" and j["step_tries"] == 1 and j["not_before"] > time.time()


def test_a_job_that_was_pushed_before_a_crash_moves_on(tmp_path, monkeypatch):
    c = bare(tmp_path, status="needs_push", head="a" * 40)
    monkeypatch.setattr(gitops, "push_ref", lambda *a: None)
    loop.push_job(c, "shell")
    assert c.st["jobs"]["shell"]["status"] == "needs_gate"


def test_an_existing_job_branch_keeps_the_run_waiting_and_names_it(feature):
    repo, _ = feature()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), f"ark/{SLUG}-ui")
    loop.cycle()
    st = state()
    assert st["status"] == "waiting" and f"ark/{SLUG}-ui already exists" in st["reason"]
    assert gitops.remote_sha(repo, f"ark/{SLUG}") is None and not proc.active(paths.run_dir("demo", SLUG))
    gitops.git(repo, "push", "-q", "origin", "--delete", f"ark/{SLUG}-ui")
    drive(jobs_settled)
    assert state()["jobs"]["ui"]["status"] == "merged"


def test_is_ancestor(feature):
    repo, _ = feature()
    head = gitops.rev(repo, "HEAD")
    child = gitops.git(repo, "commit-tree", gitops.git(repo, "rev-parse", "HEAD^{tree}"), "-p", "HEAD", "-m", "c")
    assert gitops.is_ancestor(repo, head, child) and not gitops.is_ancestor(repo, child, head)
    assert not gitops.is_ancestor(repo, "0" * 40, head)


# -- text from a model or a worker

BAD_TEXT = [("nul", "a\x00b", "ab"), ("surrogate", "a\ud800b", "a?b")]


@pytest.mark.parametrize("name,dirty,clean", BAD_TEXT)
def test_clean_makes_text_safe_at_every_depth(name, dirty, clean):
    assert loop._clean(dirty) == clean
    assert loop._clean({"k": [dirty, {"x": dirty}], "n": 1, "t": None}) == {"k": [clean, {"x": clean}], "n": 1, "t": None}
    assert loop._clean("é ok\n") == "é ok\n"


@pytest.mark.parametrize("name,dirty,clean", BAD_TEXT)
def test_a_review_finding_with_bad_text_is_stored_clean(tmp_path, name, dirty, clean):
    c = bare(tmp_path, status="reviewing")
    loop.on_review(c, "shell", receipt(json.dumps({"verdict": "FIX", "findings": [dirty]})))
    j = c.st["jobs"]["shell"]
    assert clean in j["resume_msg"] and dirty not in j["resume_msg"]
    assert merge.read_record(tmp_path, "shell", "verdict")["findings"] == [clean]


@pytest.mark.parametrize("name,dirty,clean", BAD_TEXT)
def test_a_euklides_note_with_bad_text_is_stored_clean(tmp_path, ark_home, name, dirty, clean):
    c = at_euklides(tmp_path, session="s")
    loop.on_euklides(c, "shell", receipt(json.dumps({"action": "retry", "note": dirty})))
    j = c.st["jobs"]["shell"]
    assert j["note"].endswith(clean) and clean in j["resume_msg"] and dirty not in j["resume_msg"]


@pytest.mark.parametrize("name,dirty,clean", BAD_TEXT)
def test_a_red_check_with_bad_text_is_stored_clean(tmp_path, name, dirty, clean):
    c = bare(tmp_path, status="integrating")
    line = json.dumps({"ok": False, "base": "b" * 40, "head": "abc", "reason": dirty, "tail": dirty})
    loop.on_integrate(c, "shell", receipt(line))
    assert dirty not in c.st["jobs"]["shell"]["resume_msg"] and clean in c.st["jobs"]["shell"]["resume_msg"]
    assert merge.read_record(tmp_path, "shell", "integrate")["tail"] == clean


@pytest.mark.parametrize("name,dirty,clean", BAD_TEXT)
def test_a_failure_or_block_with_bad_text_is_stored_clean(tmp_path, name, dirty, clean):
    c = bare(tmp_path)
    loop.fail(c, "shell", dirty)
    assert c.st["jobs"]["shell"]["failure"] == clean
    loop.block(c, "shell", dirty)
    assert c.st["jobs"]["shell"]["reason"] == clean and dirty not in c.st["alerts"][0]
    run.render(c.st).encode("utf-8")


@pytest.mark.parametrize("name,dirty,clean", BAD_TEXT)
def test_a_red_gate_with_bad_text_is_stored_clean(tmp_path, name, dirty, clean):
    c = bare(tmp_path, status="gating")
    loop.on_gate(c, "shell", receipt(dirty, exit=1))
    assert merge.read_record(tmp_path, "shell", "gate")["tail"] == clean


@pytest.mark.parametrize("finding", [r'"a\u0000b"', r'"a\ud800b"'])
def test_a_finding_with_bad_text_cannot_wedge_the_job(feature, monkeypatch, finding):
    monkeypatch.setenv("FAKE_FIX", "shell")
    monkeypatch.setenv("FAKE_FINDING", finding)
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 2


def test_a_report_with_a_nul_byte_cannot_wedge_the_job(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1")
    feature()
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True, exist_ok=True)
    (d / "shell.report.md").write_bytes(b"before\x00after \xff")
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and "errors" not in j


# -- the report file is the worker's to write

def _report_at(path):
    return loop.Ctx.read_report(SimpleNamespace(report=lambda job: path), "x")


def test_a_fifo_at_the_report_path_is_not_a_report(tmp_path):
    f = tmp_path / "r.md"
    os.mkfifo(f)
    old = signal.signal(signal.SIGALRM, lambda *_: pytest.fail("reading a FIFO blocked the loop"))
    signal.alarm(5)
    try:
        assert _report_at(f) is None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def test_a_directory_at_the_report_path_is_not_a_report(tmp_path):
    assert _report_at(tmp_path) is None


def test_a_symlink_at_the_report_path_is_not_followed(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("a secret")
    (tmp_path / "r.md").symlink_to(secret)
    assert _report_at(tmp_path / "r.md") is None


def test_a_missing_report_is_none(tmp_path):
    assert _report_at(tmp_path / "r.md") is None


def test_a_report_with_bad_bytes_is_read_clean(tmp_path):
    f = tmp_path / "r.md"
    f.write_bytes(b"ok \x00here \xff end \xc3\xa9")
    assert _report_at(f) == "ok here � end é"


def test_a_huge_report_is_cut(tmp_path):
    f = tmp_path / "r.md"
    f.write_bytes(b"x" * (loop.REPORT_MAX + 1000))
    assert _report_at(f) == "x" * loop.REPORT_MAX


# -- a state that cannot be saved

def _worker_runs(tmp_path, name="shell"):
    f = tmp_path / "fake" / f"{name}.worker.count"
    return int(f.read_text()) if f.exists() else 0


def test_a_save_that_keeps_failing_stops_the_spending(feature, tmp_path, monkeypatch):
    feature()
    loop.cycle()  # saved: the worker is on record

    def broken(st):
        raise OSError("read-only")

    reaped, alerts, real_reap = [], [], proc.reap
    monkeypatch.setattr(proc, "reap", lambda d: (reaped.append(d), real_reap(d))[1])
    with monkeypatch.context() as m:
        m.setattr(run, "save", broken)
        for _ in range(20):
            alerts += loop.cycle()[1]
            time.sleep(0.05)
    assert _worker_runs(tmp_path) <= 2
    assert sum("cannot be saved" in a for a in alerts) == 1
    assert len(reaped) >= 19  # still enforcing deadlines while it does nothing else
    drive(jobs_settled)
    assert all(j["status"] == "merged" for j in state()["jobs"].values())
    assert _worker_runs(tmp_path) <= 2


def test_a_failed_save_does_not_stop_the_cycle(feature, monkeypatch):
    feature()
    monkeypatch.setattr(run, "save", lambda st: (_ for _ in ()).throw(KeyError("x")))
    line, alerts, changed = loop.cycle()
    assert changed and any("cannot be saved" in a for a in alerts)


# -- approving a held run again

def _hold_and_edit(p, text="\n<!-- edited -->\n"):
    """Edit the plan under a running run, so the loop holds it."""
    p.write_text(p.read_text() + text)


def test_approving_a_held_run_again_releases_it(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    assert state()["status"] == "running"
    _hold_and_edit(p)
    _, alerts, _ = loop.cycle()
    assert state()["status"] == "held" and any("plan changed" in a for a in alerts)
    approve(p, "demo", SLUG)
    loop.cycle()
    st = state()
    assert st["status"] == "running" and st["plan_sha"] == sha256_file(p)
    assert any("approved again" in e for e in st["events"])
    drive(jobs_settled)
    assert all(j["status"] == "merged" for j in state()["jobs"].values())


def test_an_edit_with_no_new_approval_stays_held(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    _hold_and_edit(p)
    for _ in range(3):
        loop.cycle()
    assert state()["status"] == "held"


def _reapproved_with(p, m, repo="demo", slug=SLUG):
    p.write_text(plan_text(m, repo=repo, slug=slug))
    approve(p, repo, slug)


def test_a_new_approval_that_changes_the_jobs_stays_held_and_says_so(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    old = state()["plan_sha"]
    _reapproved_with(p, manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]), job("other", ["T3"], ["other/**"])]))
    alerts = []
    for _ in range(4):
        alerts += loop.cycle()[1]
    st = state()
    assert st["status"] == "held" and st["plan_sha"] == old
    assert sum("cannot take the new approval" in a and "jobs" in a for a in alerts) == 1


def test_a_new_approval_that_overlaps_another_run_stays_held(feature, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    other = make_feature(plan_text(manifest(jobs=[job("dbx", ["T1", "T2", "T3"], ["src/c/**"])]), slug="second"),
                         repo="demo", slug="second", create_repo=False)
    approve(other, "demo", "second")
    loop.cycle()
    assert state(slug="second")["status"] == "running"
    old = state()["plan_sha"]
    _reapproved_with(p, manifest(jobs=[job("shell", ["T1", "T2"], ["src/c/**"]), job("ui", ["T3"], ["src/ui/**"], after=["shell"])]))
    alerts = []
    for _ in range(3):
        alerts += loop.cycle()[1]
    st = state()
    assert st["status"] == "held" and st["plan_sha"] == old
    assert sum("cannot take the new approval" in a and "second" in a for a in alerts) == 1


def test_an_approval_whose_file_differs_is_not_adopted(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    old = state()["plan_sha"]
    _hold_and_edit(p)
    approve(p, "demo", SLUG)
    _hold_and_edit(p, "\n<!-- edited after the approval -->\n")
    alerts = [a for _ in range(3) for a in loop.cycle()[1]]
    st = state()
    assert st["status"] == "held" and st["plan_sha"] == old
    assert not [a for a in alerts if "new approval" in a]  # an edit in progress is no refusal: nothing to say yet


# -- the guards, one by one: each test fails when its guard is removed

def test_a_red_job_gate_goes_to_euklides_and_is_recorded_red(tmp_path):
    c = bare(tmp_path, status="gating")
    loop.on_gate(c, "shell", receipt("assert failed", exit=1))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and j["tier_attempts"] == 1 and "job gate red" in j["failure"]
    rec = merge.read_record(tmp_path, "shell", "gate")
    assert rec["ok"] is False and rec["exit"] == 1 and "assert failed" in rec["tail"]


def test_a_job_whose_gate_stays_red_gets_no_pr_and_never_merges(feature):
    red = job("shell", ["T1", "T2"], ["src/shell/**"], gate="exit 1")
    repo, _ = feature(manifest(jobs=[red, job("ui", ["T3"], ["src/ui/**"], after=["shell"])]))
    drive(stopping(jobs_settled))
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and st["jobs"]["shell"]["pr"] is None
    assert st["jobs"]["ui"]["status"] == "blocked" and forge.LocalForge().list(repo) == []
    assert merge.read_record(paths.run_dir("demo", SLUG), "shell", "gate")["ok"] is False


def test_a_gate_stopped_by_its_time_limit_is_red_even_with_exit_zero(tmp_path):
    c = bare(tmp_path, status="gating")
    loop.on_gate(c, "shell", receipt(exit=0, reason="timeout: over its time limit"))
    assert c.st["jobs"]["shell"]["status"] == "needs_euklides"
    assert merge.read_record(tmp_path, "shell", "gate")["ok"] is False


def test_a_green_gate_opens_the_way_to_the_pr(tmp_path):
    c = bare(tmp_path, status="gating")
    loop.on_gate(c, "shell", receipt("ok"))
    assert c.st["jobs"]["shell"]["status"] == "needs_pr" and merge.read_record(tmp_path, "shell", "gate")["ok"] is True


def test_records_name_the_commit_the_process_saw_not_the_jobs_latest(tmp_path):
    c = bare(tmp_path, status="gating", head="moved-since")
    loop.on_gate(c, "shell", receipt("ok"))
    assert merge.read_record(tmp_path, "shell", "gate")["head"] == "abc"
    c.st["jobs"]["shell"]["status"] = "reviewing"
    loop.on_review(c, "shell", receipt(json.dumps({"verdict": "CLEAN", "findings": []})))
    assert merge.read_record(tmp_path, "shell", "verdict")["head"] == "abc"


def test_an_integration_record_prefers_the_head_the_check_reports(tmp_path):
    line = {"ok": True, "base": "b" * 40, "head": "checked", "reason": "", "tail": ""}
    c = bare(tmp_path, status="integrating")
    loop.on_integrate(c, "shell", receipt(json.dumps(line)))
    assert merge.read_record(tmp_path, "shell", "integrate")["head"] == "checked"


def test_a_good_last_commit_does_not_hide_an_earlier_one_outside_the_area(feature, monkeypatch):
    monkeypatch.setenv("FAKE_EARLY_OUTSIDE", "shell")
    feature()
    drive(stopping(jobs_settled))
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and asked_marco("outside/shell.txt") and j["pr"] is None


def test_done_with_uncommitted_changes_is_a_failure(feature, monkeypatch):
    monkeypatch.setenv("FAKE_DIRTY", "shell:1")
    feature()
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "merged" and st["jobs"]["shell"]["attempts"] == 2
    assert any("uncommitted" in e for e in st["events"])


def test_done_with_no_change_is_a_failure(feature, monkeypatch):
    monkeypatch.setenv("FAKE_EMPTY", "shell:1")
    feature()
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "merged" and st["jobs"]["shell"]["attempts"] == 2
    assert any("without any change" in e for e in st["events"])


def test_review_rounds_have_a_limit(tmp_path):
    fix = json.dumps({"verdict": "FIX", "findings": ["again"]})
    c = bare(tmp_path, status="reviewing", review_rounds=1)
    loop.on_review(c, "shell", receipt(fix))
    assert c.st["jobs"]["shell"]["status"] == "needs_worker"
    c.st["jobs"]["shell"]["status"] = "reviewing"
    loop.on_review(c, "shell", receipt(fix))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and "3 review rounds" in j["failure"] and j["tier_attempts"] == 1


def test_a_verdict_that_is_neither_clean_nor_fix_is_no_verdict(tmp_path):
    c = bare(tmp_path, status="reviewing")
    loop.on_review(c, "shell", receipt(json.dumps({"verdict": "MAYBE", "findings": ["x"]})))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_review" and j["review_errors"] == 1 and j["review_rounds"] == 0


def test_an_escalation_starts_its_review_rounds_over(tmp_path, ark_home):
    c = at_euklides(tmp_path, review_rounds=2, session="s")
    loop.on_euklides(c, "shell", receipt(json.dumps({"action": "escalate", "note": "harder"})))
    j = c.st["jobs"]["shell"]
    assert j["tier"] == "T2" and j["review_rounds"] == 0 and j["session"] is None


def test_a_check_stopped_by_a_reason_is_not_believed_even_with_a_green_line(tmp_path):
    c = bare(tmp_path, status="integrating")
    line = json.dumps({"ok": True, "base": "b" * 40, "head": "abc", "reason": "", "tail": ""})
    loop.on_integrate(c, "shell", receipt(line, reason="stopped: timeout"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_integrate" and j["integrate_lost"] == 1
    assert merge.read_record(tmp_path, "shell", "integrate") is None


def test_green_needs_a_base_that_is_a_string(tmp_path):
    c = bare(tmp_path, status="integrating")
    loop.on_integrate(c, "shell", receipt(json.dumps({"ok": True, "base": 123, "head": "abc", "reason": "", "tail": ""})))
    assert c.st["jobs"]["shell"]["status"] == "needs_worker"
    assert merge.read_record(tmp_path, "shell", "integrate")["ok"] is False


def _second_plan(make_feature, m=None, at="2026-11-05T11:00:00+00:00"):
    p = make_feature(plan_text(m, slug="second"), repo="demo", slug="second", create_repo=False)
    approve(p, "demo", "second", at=at)
    return p


def test_a_plan_overlapping_a_running_one_waits(feature, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    repo, _ = feature()
    _second_plan(make_feature)
    loop.cycle()
    st = state(slug="second")
    assert state()["status"] == "running" and st["status"] == "waiting" and "overlaps" in st["reason"]
    assert gitops.remote_sha(repo, "ark/second") is None


def test_a_held_plan_still_owns_its_area(feature, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    p.write_text(p.read_text() + "\n<!-- edited -->\n")
    loop.cycle()
    assert state()["status"] == "held"
    _second_plan(make_feature)
    loop.cycle()
    assert state(slug="second")["status"] == "waiting" and "overlaps" in state(slug="second")["reason"]


def test_a_plan_that_no_longer_validates_never_starts(feature):
    repo, p = feature()
    (p.parent / "spec.md").unlink()
    loop.cycle()
    st = state()
    assert st["status"] == "waiting" and "does not validate" in st["reason"]
    assert gitops.remote_sha(repo, f"ark/{SLUG}") is None


SLOT_LIMITS = {"writers_per_repo": 1, "writers_total": 2, "heavy_gates": 1}


def _slot_state(status, heavy=False, repo="a"):
    return {"repo": repo, "jobs": {"j": {"status": status, "heavy": heavy}}}


def test_slots_hold_the_writers_of_one_repo():
    s = loop.Slots([], SLOT_LIMITS)
    assert s.writer("a") and not s.writer("a") and s.writer("b")


def test_slots_hold_the_writers_in_total():
    s = loop.Slots([], {**SLOT_LIMITS, "writers_per_repo": 9})
    assert s.writer("a") and s.writer("a") and not s.writer("b")


def test_slots_hold_the_heavy_gates_and_never_the_light_ones():
    s = loop.Slots([], SLOT_LIMITS)
    assert s.gate(False) and s.gate(True) and not s.gate(True) and s.gate(False)


@pytest.mark.parametrize("status", ["gating", "integrating"])
def test_slots_count_the_heavy_gates_already_running(status):
    assert not loop.Slots([_slot_state(status, heavy=True)], SLOT_LIMITS).gate(True)
    assert loop.Slots([_slot_state(status)], SLOT_LIMITS).gate(True)


def test_slots_count_the_workers_already_running():
    assert not loop.Slots([_slot_state("working")], SLOT_LIMITS).writer("a")
    assert loop.Slots([_slot_state("needs_worker")], SLOT_LIMITS).writer("a")


def test_a_pr_is_created_once(tmp_path):
    c = bare(tmp_path, status="needs_pr", pr={"number": 1, "url": "u"})
    c.forge = SimpleNamespace(create=lambda *a, **k: pytest.fail("a second PR"))
    loop.ensure_pr(c, "shell")
    assert c.st["jobs"]["shell"]["status"] == "needs_review"


def test_the_base_is_on_disk_before_the_branch_exists(feature, monkeypatch):
    feature()
    real, seen = gitops.push_ref, []
    def spy(repo, sha, branch):
        if branch == f"ark/{SLUG}":
            seen.append((sha, run.load("demo", SLUG)["base_sha"]))
        return real(repo, sha, branch)
    monkeypatch.setattr(gitops, "push_ref", spy)
    loop.cycle()
    assert len(seen) == 1 and seen[0][0] == seen[0][1]


def _advance_from_elsewhere(repo, tmp_path, branch):
    """Someone else moves the branch: nothing in `repo` knows until it fetches."""
    other = tmp_path / "elsewhere"
    subprocess.run(["git", "clone", "-q", "-b", branch, gitops.origin_url(repo), str(other)], check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "moved"], cwd=other, check=True)
    subprocess.run(["git", "push", "-q", "origin", branch], cwd=other, check=True)


@pytest.fixture
def at_merge(feature, monkeypatch):
    """A job with every check green, one step from its merge."""
    repo, _ = feature()
    with monkeypatch.context() as m:
        m.setattr(loop, "do_merge", lambda c, job: None)
        drive(lambda: state() and state()["jobs"].get("shell", {}).get("status") == "needs_merge")
    st = state()
    return repo, st, loop.Ctx(st, forge.LocalForge())


def test_a_base_that_moved_since_the_check_goes_back_to_the_check(at_merge, tmp_path):
    repo, st, c = at_merge
    _advance_from_elsewhere(repo, tmp_path, f"ark/{SLUG}")
    loop.do_merge(c, "shell")
    assert st["jobs"]["shell"]["status"] == "needs_integrate"


def test_a_refused_merge_blocks_and_never_reaches_euklides(at_merge, monkeypatch):
    _, st, c = at_merge
    def refused(*a):
        raise merge.MergeRefused("the PR head moved")
    monkeypatch.setattr(merge, "merge", refused)
    loop.do_merge(c, "shell")
    j = st["jobs"]["shell"]
    assert j["status"] == "blocked" and "the PR head moved" in j["reason"] and j["tier_attempts"] == 0


@pytest.mark.parametrize("field", ["events", "alerts"])
def test_a_state_with_the_wrong_list_is_damage(feature, field):
    feature()
    loop.cycle()
    st = state()
    st[field] = "not a list"
    run.state_file("demo", SLUG).write_text(json.dumps(st))
    with pytest.raises(run.StateCorrupt):
        loop._load("demo", SLUG)


def test_a_state_of_another_run_is_damage(feature):
    feature()
    loop.cycle()
    st = state()
    st["slug"] = "somebody-else"
    run.state_file("demo", SLUG).write_text(json.dumps(st))
    with pytest.raises(run.StateCorrupt):
        loop._load("demo", SLUG)


@pytest.mark.parametrize("bad", [{"approved_at": 5}, {"plan": None}, {"sha256": ["x"]}])
def test_an_approval_with_a_field_of_the_wrong_type_is_no_approval(ark_home, bad):
    good = {"repo": "demo", "slug": SLUG, "plan": "/p", "sha256": "0" * 64, "approved_at": "t"}
    d = paths.approvals_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "good.json").write_text(json.dumps(good))
    (d / "bad.json").write_text(json.dumps({**good, "slug": "other", **bad}))
    assert [a["slug"] for a in loop.approvals()] == [SLUG]


def test_a_held_run_keeps_enforcing_the_time_limits(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    p.write_text(p.read_text() + "\n<!-- edited -->\n")
    reaped, real = [], proc.reap
    monkeypatch.setattr(proc, "reap", lambda d: (reaped.append(d), real(d))[1])
    loop.cycle()
    assert state()["status"] == "held" and reaped == [paths.run_dir("demo", SLUG)]


@pytest.fixture
def worker_starts(feature, monkeypatch):
    """A run whose worker starts are recorded instead of run."""
    feature(manifest(setup=["echo set up"], jobs=[job("shell", ["T1", "T2", "T3"], ["src/shell/**"])]))
    starts = []
    monkeypatch.setattr(proc, "start", lambda d, name, cmd, **k: starts.append({"name": name, "cmd": cmd, **k}))
    loop.cycle()
    st = state()
    return st, loop.Ctx(st, forge.LocalForge()), starts


def test_setup_runs_until_it_is_done(worker_starts):
    st, c, starts = worker_starts
    assert starts[-1]["pre"] == ["echo set up"]
    st["jobs"]["shell"].update(status="needs_worker", setup_done=True)
    loop.start_worker(c, "shell")
    assert starts[-1]["pre"] == []


def test_a_new_attempt_does_not_inherit_the_last_ones_done_blocks(worker_starts):
    st, c, _ = worker_starts
    f = c.dir / "shell.doneblocks"
    f.write_text("3")
    loop.start_worker(c, "shell")
    assert not f.exists()


def test_a_session_resumes_only_with_something_to_say(worker_starts):
    st, c, starts = worker_starts
    j = st["jobs"]["shell"]
    j.update(session="s1", resume_msg=None)
    loop.start_worker(c, "shell")
    assert "--resume" not in starts[-1]["cmd"] and j["session"] != "s1"
    j.update(session="s1", resume_msg="fix this")
    loop.start_worker(c, "shell")
    cmd = starts[-1]["cmd"]
    assert cmd[cmd.index("--resume") + 1] == "s1" and cmd[cmd.index("-p") + 1] == "fix this"


# -- an ignored receipt still counts

def test_an_ignored_receipt_still_charges_and_still_warns_of_survivors(tmp_path, monkeypatch):
    c = bare(tmp_path, status="needs_gate")  # the job moved on: nothing waits for this worker's result
    consume_one(monkeypatch, c, "shell.worker", receipt(usd=0.5, survivors=[123], reason="over its time limit"))
    assert c.st["jobs"]["shell"]["status"] == "needs_gate" and c.st["jobs"]["shell"]["usd"] == 0.5
    assert any("outlived" in a for a in c.st["alerts"])
    assert any("ignored" in e for e in c.st["events"])


def test_a_receipt_of_no_known_job_warns_of_survivors_and_charges_nobody(tmp_path, monkeypatch):
    c = bare(tmp_path)
    consume_one(monkeypatch, c, "ghost.worker", receipt(usd=0.5, survivors=[123]))
    assert c.st["jobs"]["shell"]["usd"] == 0 and any("ghost" in a and "outlived" in a for a in c.st["alerts"])


# -- progress is on disk before the next step pays for anything

def _crash_after_schedule(monkeypatch):
    """The loop dies right after this cycle's scheduling, before the save at its end."""
    real = loop.schedule
    def dies(c, slots):
        real(c, slots)
        raise KeyboardInterrupt
    monkeypatch.setattr(loop, "schedule", dies)


def test_a_crash_right_after_a_receipt_does_not_pay_for_the_worker_again(feature, monkeypatch, tmp_path):
    feature()
    loop.cycle()
    def finished():
        return bool(proc.finished(paths.run_dir("demo", SLUG)))
    for _ in range(100):
        if finished():
            break
        time.sleep(0.05)
    assert state()["jobs"]["shell"]["status"] == "working" and finished()
    with monkeypatch.context() as m:
        m.setattr(loop, "schedule", lambda c, slots: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            loop.cycle()
    assert state()["jobs"]["shell"]["status"] == "needs_push"
    drive(jobs_settled)
    assert state()["jobs"]["shell"]["status"] == "merged" and _worker_runs(tmp_path) == 1


def test_a_stop_is_not_forgotten_by_a_crash_right_after_it(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    feature()
    loop.cycle()
    d = paths.run_dir("demo", SLUG)
    assert proc.stop(d, "shell.worker", "stopped by Marco")
    with monkeypatch.context() as m:
        m.setattr(loop, "schedule", lambda c, slots: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            loop.cycle()
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and j["reason"] == "stopped by Marco"


def test_a_crash_in_the_first_cycle_does_not_start_the_worker_twice(feature, monkeypatch, tmp_path):
    feature()
    with monkeypatch.context() as m:
        _crash_after_schedule(m)
        with pytest.raises(KeyboardInterrupt):
            loop.cycle()
    d = paths.run_dir("demo", SLUG)
    for _ in range(100):  # the worker ends during the downtime
        if proc.finished(d):
            break
        time.sleep(0.05)
    drive(jobs_settled)
    assert state()["jobs"]["shell"]["status"] == "merged" and _worker_runs(tmp_path) == 1


def test_a_save_that_fails_between_receipts_loses_none_of_them(feature, monkeypatch):
    feature(manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]), job("ui", ["T3"], ["src/ui/**"])]))
    loop.cycle()
    d = paths.run_dir("demo", SLUG)
    for _ in range(100):
        if len(proc.finished(d)) == 2:
            break
        time.sleep(0.05)
    assert len(proc.finished(d)) == 2
    with monkeypatch.context() as m:
        m.setattr(run, "save", lambda st: (_ for _ in ()).throw(OSError("disk full")))
        loop.cycle()
    assert proc.finished(d) == []


# -- a run whose repo is gone keeps its deadlines

def test_a_run_whose_repo_is_gone_keeps_enforcing_the_time_limits(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    repo, _ = feature()
    loop.cycle()
    shutil.rmtree(repo)
    reaped, real = [], proc.reap
    monkeypatch.setattr(proc, "reap", lambda d: (reaped.append(d), real(d))[1])
    alerts = loop.cycle()[1]
    assert reaped == [paths.run_dir("demo", SLUG)] and any("runner error" in a for a in alerts)


# -- a run with no valid approval does not vanish

def _spy_reap(monkeypatch):
    reaped, real = [], proc.reap
    monkeypatch.setattr(proc, "reap", lambda d: (reaped.append(d), real(d))[1])
    return reaped


def test_a_live_run_that_lost_its_approval_alerts_once_and_keeps_its_deadlines(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    feature()
    loop.cycle()
    good = paths.approval_file("demo", SLUG).read_text()
    paths.approval_file("demo", SLUG).unlink()
    reaped = _spy_reap(monkeypatch)
    alerts = loop.cycle()[1]
    assert [a for a in alerts if "no valid approval" in a and f"demo/{SLUG}" in a] == alerts and len(alerts) == 1
    assert reaped == [paths.run_dir("demo", SLUG)]
    assert loop.cycle()[1] == [] and len(reaped) == 2  # said once, reaped always
    paths.approval_file("demo", SLUG).write_text(good)
    assert not [a for a in loop.cycle()[1] if "no valid approval" in a]
    paths.approval_file("demo", SLUG).write_text("not json")  # back, and lost again: Marco hears again
    assert [a for a in loop.cycle()[1] if "no valid approval" in a]


def test_a_finished_run_needs_no_approval(feature):
    feature()
    loop.cycle()
    st = state()
    st["status"] = "done"
    run.save(st)
    paths.approval_file("demo", SLUG).unlink()
    assert loop.cycle()[1] == []


def test_a_damaged_run_with_no_approval_is_not_taken_for_finished(feature, monkeypatch):
    feature()
    loop.cycle()
    run.state_file("demo", SLUG).write_text("not json")
    paths.approval_file("demo", SLUG).unlink()
    reaped = _spy_reap(monkeypatch)
    assert [a for a in loop.cycle()[1] if "no valid approval" in a] and reaped == [paths.run_dir("demo", SLUG)]


def test_a_run_in_a_nested_repo_is_found_when_it_loses_its_approval(ark_home):
    d = paths.run_dir("group/demo", SLUG)
    d.mkdir(parents=True)
    st = run.new_state({"repo": "group/demo", "slug": SLUG, "plan": "/p", "sha256": "0" * 64, "approved_at": "t"})
    run.save(st)
    assert [a for a in loop.cycle(forge.LocalForge())[1] if f"group/demo/{SLUG}" in a]


# -- a worktree is checked before a worker is paid for

def test_a_worktree_left_on_another_branch_gets_no_worker(feature, monkeypatch, tmp_path):
    repo, _ = feature()
    with monkeypatch.context() as m:
        m.setattr(loop, "schedule", lambda c, slots: None)
        loop.cycle()
    wt = loop.Ctx(state(), forge.LocalForge()).wt("shell")
    gitops.add_worktree(repo, wt, "somebody-elses", "HEAD")
    loop.cycle()
    j = state()["jobs"]["shell"]
    assert j["status"] == "pending" and j["attempts"] == 0 and j["step_tries"] == 1
    assert any("not a worktree" in e for e in state()["events"])
    assert not (tmp_path / "fake" / "shell.worker.count").exists()


def test_a_second_attempt_resumes_in_the_worktree_it_has(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1")
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 2 and j["base"]


# -- a merged job stays merged

def test_a_merge_the_state_never_recorded_is_seen_before_the_next_check(at_merge, monkeypatch, tmp_path):
    repo, st, c = at_merge
    merge.merge(st, "shell", c.forge, repo)  # then a crash: the state still says it has to be checked
    st["jobs"]["shell"]["status"] = "needs_integrate"
    monkeypatch.setattr(proc, "start", lambda *a, **k: pytest.fail("a check of a merged job"))
    loop.start_integrate(c, "shell")
    assert st["jobs"]["shell"]["status"] == "merged"


def test_a_job_with_no_pr_goes_to_its_check(at_merge, monkeypatch):
    _, st, c = at_merge
    started = []
    monkeypatch.setattr(proc, "start", lambda d, name, *a, **k: started.append(name))
    st["jobs"]["shell"].update(status="needs_integrate", pr=None)
    loop.start_integrate(c, "shell")
    assert st["jobs"]["shell"]["status"] == "integrating" and started == ["shell.integrate"]


# ---- second review -------------------------------------------------------------------------------

def shim(tmp_path, monkeypatch, body):
    """Run `body` in place of claude; it can hand over to the usual fake with `exec "$REAL_FAKE" "$@"`."""
    s = tmp_path / "shim.sh"
    s.write_text("#!/bin/sh\n" + body)
    s.chmod(0o755)
    monkeypatch.setenv("REAL_FAKE", os.environ["ARK_CLAUDE"])
    monkeypatch.setenv("ARK_CLAUDE", str(s))


def turn(n, sleep=0.05):
    out = []
    for _ in range(n):
        out += loop.cycle()[1]
        time.sleep(sleep)
    return out


def runs_of(job_name, kind):
    return len(list((paths.run_dir("demo", SLUG) / "done").glob(f"{job_name}.{kind}-*.json")))


# -- a new approval: everything not merged earns its checks again, under the plan approved now

STRICTER = {"job gate": {"jobs": [job("shell", ["T1", "T2"], ["src/shell/**"], gate="false"),
                                  job("ui", ["T3"], ["src/ui/**"], after=["shell"])]},
            "integration gate": {"integration_gate": "false"}}


@pytest.mark.parametrize("what", STRICTER)
def test_a_job_past_its_checks_runs_the_gates_of_the_plan_approved_now(at_merge, monkeypatch, what):
    monkeypatch.setattr(loop, "BACKOFF_FIRST", 0)
    _, st, _ = at_merge
    p = Path(st["plan"])
    p.write_text(plan_text(manifest(**STRICTER[what])))
    loop.cycle()
    approve(p, "demo", SLUG)
    loop.cycle()
    st = state()
    assert st["status"] == "running" and st["plan_sha"] == sha256_file(p)
    drive(stopping(jobs_settled))
    assert state()["jobs"]["shell"]["status"] == "blocked"


def test_a_new_approval_leaves_no_green_record_for_a_terminal_merge(at_merge, monkeypatch):
    repo, st, c = at_merge
    p = Path(st["plan"])
    _hold_and_edit(p)
    loop.cycle()
    approve(p, "demo", SLUG)
    with monkeypatch.context() as m:
        m.setattr(loop, "start_gate", lambda c, job: None)                      # only the adoption happens
        loop.cycle()
    st = state()
    assert st["status"] == "running" and st["jobs"]["shell"]["status"] == "needs_gate"
    assert not any(merge.read_record(paths.run_dir("demo", SLUG), "shell", k) for k in ("gate", "verdict", "integrate"))
    assert "no green job gate" in merge.check(st, "shell", forge.LocalForge(), repo)


def test_even_a_new_approval_that_only_touches_a_comment_earns_the_checks_again(at_merge):
    _, st, _ = at_merge
    p = Path(st["plan"])
    _hold_and_edit(p)
    loop.cycle()
    approve(p, "demo", SLUG)
    drive(jobs_settled)
    assert state()["jobs"]["shell"]["status"] == "merged"
    assert (runs_of("shell", "gate"), runs_of("shell", "review"), runs_of("shell", "integrate")) == (2, 2, 2)
    assert runs_of("shell", "worker") == 1                                      # no worker pays for it


def _at_gating(feature, gate="sleep 1"):
    r, p = feature(manifest(jobs=[job("shell", ["T1", "T2", "T3"], ["src/shell/**"], gate=gate)]))
    drive(lambda: state() and state()["jobs"].get("shell", {}).get("status") == "gating", cycles=200)
    return r, p


def test_a_crash_while_taking_a_new_approval_keeps_no_old_green(at_merge, monkeypatch):
    _, st, _ = at_merge
    p = Path(st["plan"])
    sha = st["plan_sha"]
    _hold_and_edit(p)
    loop.cycle()
    approve(p, "demo", SLUG)
    with monkeypatch.context() as m:
        m.setattr(run, "save", lambda st: (_ for _ in ()).throw(OSError("disk full")))
        loop.cycle()
    assert state()["plan_sha"] == sha and not merge.read_record(paths.run_dir("demo", SLUG), "shell", "gate")
    drive(jobs_settled)
    st = state()
    assert st["plan_sha"] == sha256_file(p) and st["jobs"]["shell"]["status"] == "merged"
    assert runs_of("shell", "gate") >= 2                                        # a gate that ran unsaved repeats, never counts


def test_a_gate_that_started_under_the_old_plan_does_not_count(feature, monkeypatch):
    monkeypatch.setattr(loop, "BACKOFF_FIRST", 0)
    _, p = _at_gating(feature)
    p.write_text(plan_text(manifest(jobs=[job("shell", ["T1", "T2", "T3"], ["src/shell/**"], gate="false")])))
    loop.cycle()
    approve(p, "demo", SLUG)
    loop.cycle()
    assert state()["status"] == "held"                                          # it still runs: nothing to believe yet
    drive(stopping(jobs_settled))
    assert runs_of("shell", "gate") >= 2 and state()["jobs"]["shell"]["status"] == "blocked"


def test_a_stop_from_marco_during_the_hold_survives_a_new_approval(feature):
    _, p = _at_gating(feature, gate="sleep 30")
    _hold_and_edit(p)
    loop.cycle()
    proc.stop(paths.run_dir("demo", SLUG), "shell.gate", "stopped by Marco")
    approve(p, "demo", SLUG)
    loop.cycle()
    st = state()
    assert st["status"] == "running" and st["jobs"]["shell"]["status"] == "blocked"
    assert "stopped by Marco" in st["jobs"]["shell"]["reason"]


def test_a_merged_job_cannot_pass_new_gates_so_the_approval_is_refused(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    drive(lambda: state() and state()["jobs"].get("shell", {}).get("status") == "merged")
    old, sha = p.read_text(), state()["plan_sha"]
    p.write_text(plan_text(manifest(jobs=[job("shell", ["T1", "T2"], ["src/narrower/**"]),
                                          job("ui", ["T3"], ["src/ui/**"], after=["shell"])])))  # the area moves, not only grows
    alerts = turn(1)
    approve(p, "demo", SLUG)
    alerts += turn(3)
    st = state()
    assert st["status"] == "held" and st["plan_sha"] == sha
    assert sum("cannot take the new approval" in a and "shell" in a and "merged" in a for a in alerts) == 1
    p.write_text(old)
    approve(p, "demo", SLUG)
    drive(jobs_settled)
    assert state()["status"] == "done" and state()["jobs"]["ui"]["status"] == "merged"


def test_restoring_the_plan_as_told_and_approving_again_loses_nothing(at_merge, monkeypatch):
    _, st, _ = at_merge
    p = Path(st["plan"])
    old = p.read_text()
    p.write_text(plan_text(manifest(jobs=[job("shell", ["T1", "T2", "T3"], ["src/shell/**"]), job("x", ["T1"], ["x/**"])])))
    alerts = turn(1)
    approve(p, "demo", SLUG)
    alerts += turn(2)
    said = [a for a in alerts if "cannot take the new approval" in a]
    assert len(said) == 1 and "approve it again" in said[0]
    p.write_text(old)                                                           # restored, not approved yet
    alerts = turn(3)
    st = state()
    assert st["status"] == "held" and st["jobs"]["shell"]["status"] == "needs_merge" and not alerts  # he was told
    approve(p, "demo", SLUG)
    drive(jobs_settled)
    st = state()
    assert st["status"] == "done" and st["jobs"]["shell"]["status"] == "merged" and st["jobs"]["ui"]["status"] == "merged"
    assert "reapprove_refused" not in st and runs_of("shell", "gate") == 1      # all of it kept


def test_an_approval_that_disagrees_with_the_run_holds_it(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    a = paths.approval_file("demo", SLUG)
    a.write_text(json.dumps({**json.loads(a.read_text()), "sha256": "f" * 64}))
    loop.cycle()
    assert state()["status"] == "held"


def test_a_new_edge_makes_a_job_at_its_merge_wait_for_it(at_merge):
    _, st, _ = at_merge
    p = Path(st["plan"])
    p.write_text(plan_text(manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"], after=["ui"]),
                                          job("ui", ["T3"], ["src/ui/**"])])))
    loop.cycle()
    approve(p, "demo", SLUG)
    drive(jobs_settled)
    assert [j["status"] for j in state()["jobs"].values()] == ["merged", "merged"]


@pytest.mark.parametrize("dep,moves", [("pending", False), ("blocked", False)])
def test_a_job_at_its_merge_follows_its_dependency(tmp_path, dep, moves):
    c = bare(tmp_path, status="needs_merge")
    c.mjobs["shell"]["after"] = ["ui"]
    c.st["jobs"]["ui"] = {"status": dep}
    slots = loop.Slots([], {"writers_per_repo": 1, "writers_total": 1, "heavy_gates": 1})
    move = loop._next_step(c, "shell", c.st["jobs"]["shell"], slots)
    assert bool(move) is moves and c.st["jobs"]["shell"]["status"] == "needs_merge"  # it waits, never blocks


def test_a_new_approval_brings_the_fields_the_job_copied_from_the_plan(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    p.write_text(plan_text(manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"], heavy=True, tier="T2"),
                                          job("ui", ["T3"], ["src/ui/**"], after=["shell"], heavy=True, tier="T2")])))
    loop.cycle()
    approve(p, "demo", SLUG)
    loop.cycle()
    shell, ui = state()["jobs"]["shell"], state()["jobs"]["ui"]
    assert shell["heavy"] is True and shell["tier"] == "T1"                     # it already runs at the tier it has
    assert ui["heavy"] is True and ui["tier"] == "T2" and ui["tier_path"] == ["T2"]


# -- what counts as "the model never got to work"

@pytest.mark.parametrize("end", ["exit 1", "kill -KILL $$"])
def test_a_worker_that_worked_and_crashed_is_charged(feature, monkeypatch, tmp_path, end):
    monkeypatch.setattr(loop, "BACKOFF_FIRST", 0)
    shim(tmp_path, monkeypatch, f'''
if [ "$ARK_ROLE" = worker ]; then
  n=$(cat "$FAKE_STATE/w" 2>/dev/null || echo 0); echo $((n+1)) > "$FAKE_STATE/w"
  echo '{{"type":"system","subtype":"init"}}'; echo '{{"type":"assistant","message":{{"usage":{{"output_tokens":9000}}}}}}'
  git commit -q --allow-empty -m "real work $n"
  {end}
fi
exec "$REAL_FAKE" "$@"
''')
    feature()
    drive(stopping(jobs_settled))
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and "infra_tries" not in j
    assert int((tmp_path / "fake" / "w").read_text()) == j["attempts"] <= 6


def test_a_setup_blip_starts_fresh_and_never_resumes_what_never_ran(feature, monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "BACKOFF_FIRST", 0)
    mark = tmp_path / "setupfailed"
    shim(tmp_path, monkeypatch, '''
case " $* " in *" --resume "*) echo "$*" >> "$FAKE_STATE/resumes"; echo 'No conversation found' >&2; exit 1;; esac
exec "$REAL_FAKE" "$@"
''')
    feature(manifest(setup=[f"test -f {mark} || {{ touch {mark}; exit 1; }}"]))
    alerts = []
    for _ in range(300):
        alerts += loop.cycle()[1]
        if jobs_settled():
            break
        time.sleep(0.05)
    st = state()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"], st["events"][-8:]
    assert not (tmp_path / "fake" / "resumes").exists()
    assert not [a for a in alerts if "unreachable" in a]


def test_a_session_that_cannot_be_resumed_is_dropped_after_one_quick_failure(feature, monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "PASSING_WAITS", (0,) * 5)
    monkeypatch.setenv("FAKE_FIX", "shell")
    shim(tmp_path, monkeypatch, '''
case " $* " in *" --resume "*) n=$(cat "$FAKE_STATE/r" 2>/dev/null || echo 0); echo $((n+1)) > "$FAKE_STATE/r"; exit 1;; esac
exec "$REAL_FAKE" "$@"
''')
    started, real = [], proc.start
    monkeypatch.setattr(proc, "start", lambda d, name, cmd, **k: (started.append(cmd) if name == "shell.worker" else 0,
                                                                  real(d, name, cmd, **k))[1])
    feature()
    alerts = []
    for _ in range(300):
        alerts += loop.cycle()[1]
        if jobs_settled():
            break
        time.sleep(0.05)
    assert state()["jobs"]["shell"]["status"] == "merged"
    assert (tmp_path / "fake" / "r").read_text().strip() == "1"                 # tried once, never again
    assert "--resume" not in started[-1] and "src/x:1" in " ".join(started[-1])  # the feedback still reaches it
    assert not [a for a in alerts if "unreachable" in a]                        # healed at once: nothing to tell


def test_a_timed_out_worker_can_still_be_resumed(tmp_path, monkeypatch):
    c = bare(tmp_path, status="working", session="old")
    consume_one(monkeypatch, c, "shell.worker", receipt(reason="timeout: over its time limit", exit=-15, session="s1"))
    assert c.st["jobs"]["shell"]["session"] == "s1"


# -- a refused start says so, once

def test_a_leftover_job_branch_alerts_once(feature):
    repo, _ = feature()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), f"ark/{SLUG}-ui")
    alerts = turn(4, 0)
    assert sum(f"ark/{SLUG}-ui already exists" in a for a in alerts) == 1


def test_a_leftover_integration_branch_alerts_once(feature):
    repo, _ = feature()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), f"ark/{SLUG}")
    alerts = turn(4, 0)
    assert sum(f"ark/{SLUG} already exists" in a for a in alerts) == 1


def test_a_plan_that_does_not_validate_alerts_once(feature):
    _, p = feature()
    (p.parent / "spec.md").unlink()
    alerts = turn(4, 0)
    assert sum("does not validate" in a for a in alerts) == 1


def test_an_overlap_is_a_normal_wait_and_alerts_nothing(feature, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    feature()
    _second_plan(make_feature)
    alerts = turn(3, 0)
    assert "overlaps" in state(slug="second")["reason"] and not [a for a in alerts if "overlap" in a]


def test_a_plan_overlapping_a_held_run_alerts_once_and_names_it(feature, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    loop.cycle()
    p.write_text(p.read_text() + "\n<!-- edited -->\n")
    loop.cycle()
    assert state()["status"] == "held"
    _second_plan(make_feature)
    alerts = [a for a in turn(5, 0) if "overlap" in a]
    assert state(slug="second")["status"] == "waiting" and f"held plan {SLUG}" in state(slug="second")["reason"]
    assert len(alerts) == 1 and alerts[0].startswith("demo/second: ") and f"held plan {SLUG}" in alerts[0]


def test_a_plan_waiting_on_a_run_that_gets_held_alerts_once(feature, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    _second_plan(make_feature)
    assert not [a for a in turn(2, 0) if "overlap" in a]
    p.write_text(p.read_text() + "\n<!-- edited -->\n")
    alerts = [a for a in turn(5, 0) if "overlap" in a]
    assert state()["status"] == "held" and state(slug="second")["status"] == "waiting"
    assert len(alerts) == 1 and f"held plan {SLUG}" in alerts[0]


# -- errors, saves, rework

def test_the_last_error_before_euklides_is_the_one_he_sees(tmp_path):
    c = bare(tmp_path, status="needs_pr")
    for _ in range(5):
        c.st["jobs"]["shell"].pop("not_before", None)
        loop._error(c, "shell", gitops.GitError("offline"))
    assert c.st["jobs"]["shell"]["status"] == "needs_pr" and c.st["alerts"] == []
    c.st["jobs"]["shell"].pop("not_before", None)
    loop._error(c, "shell", ValueError("will not heal"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and "will not heal" in j["euklides"]["situation"]


def test_a_save_that_fails_after_a_receipt_starts_nothing_in_that_cycle(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FIX", "shell")
    feature()
    d = paths.run_dir("demo", SLUG)
    for _ in range(600):
        if "shell.review" in proc.finished(d):
            break
        loop.cycle()
        for _ in range(4):
            if "shell.review" in proc.finished(d):
                break
            time.sleep(0.02)
    assert "shell.review" in proc.finished(d)
    with monkeypatch.context() as m:
        m.setattr(run, "save", lambda st: (_ for _ in ()).throw(OSError("disk full")))
        loop.cycle()
    assert "shell.worker" not in proc.active(d)
    drive(jobs_settled)
    assert all(j["status"] == "merged" for j in state()["jobs"].values())
    assert not [e for e in state()["events"] if "ignored the result" in e]      # the FIX was not dropped


def test_a_rewritten_branch_says_what_probably_happened(tmp_path, monkeypatch):
    c = bare(tmp_path, status="needs_push", head="b" * 40)
    def refused(*a):
        raise gitops.GitError("non-fast-forward")
    monkeypatch.setattr(gitops, "push_ref", refused)
    monkeypatch.setattr(gitops, "remote_sha", lambda *a: "a" * 40)
    monkeypatch.setattr(gitops, "is_ancestor", lambda *a: False)
    loop.push_job(c, "shell")
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_push" and j["not_before"] > time.time()
    assert any("amend" in e for e in c.st["events"])


def test_workers_are_told_not_to_rewrite_their_history():
    text = (Path(loop.__file__).resolve().parents[2] / "agents" / "ark-dev.md").read_text()
    assert "amend" in text and "rebase" in text


# -- closing

def run_finished():
    st = state()
    return bool(st) and st["status"] in ("done", "blocked")


def test_run_closes_with_decisions_and_final_pr(feature):
    repo, _ = feature()
    drive(run_finished)
    st = state()
    assert st["status"] == "done"
    gitops.fetch(repo)
    text = gitops.git(repo, "show", f"origin/ark/{SLUG}:docs/decisions/{SLUG}.md")
    assert text.startswith("# Decisions")
    final = forge.LocalForge().list(repo)[-1]
    assert (final["base"], final["head"], final["title"]) == ("master", f"ark/{SLUG}", "feat(notes): edit and delete notes")
    assert final["body"].startswith(f"**Decisions:** `docs/decisions/{SLUG}.md`")
    assert st["final_pr"]["url"] == final["url"]
    assert "Status: done" in paths.run_file("demo", SLUG).read_text()


def test_blocked_run_writes_the_fallback_when_the_tick_fails(feature, monkeypatch):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    repo, _ = feature()
    monkeypatch.setattr(loop.brief, "decisions_prompt", lambda st, reports: "say nothing useful")
    drive(stopping(run_finished))
    st = state()
    assert st["status"] == "blocked" and st["final_pr"] is None             # nothing merged: no final PR
    gitops.fetch(repo)
    text = gitops.git(repo, "show", f"origin/ark/{SLUG}:docs/decisions/{SLUG}.md")
    assert "written by the runner" in text and "outside its area" in text


def test_a_symlinked_docs_on_the_branch_blocks_the_close_once(feature):
    repo, _ = feature()
    (repo / "docs").symlink_to("README.md")                                 # docs/ is a symlink on the default branch
    gitops.git(repo, "add", "docs")
    gitops.git(repo, "commit", "-q", "-m", "docs is a symlink")
    gitops.git(repo, "push", "-q", "origin", "master")
    alerts = []
    for _ in range(400):
        alerts += loop.cycle()[1]
        if run_finished():
            break
        time.sleep(0.05)
    for _ in range(3):
        alerts += loop.cycle()[1]                                           # a finished run is left alone
    st = state()
    assert st["status"] == "blocked" and st["final_pr"] is None and st["closing"] == "done"
    mine = [a for a in alerts if "symlink" in a]
    assert len(mine) == 1 and "decisions.md" in mine[0]
    assert (paths.run_dir("demo", SLUG) / "decisions.md").is_file()         # the text is kept for Marco
    assert not [p for p in forge.LocalForge().list(repo) if p["base"] == "master"]


class Crash(BaseException):
    """The runner dies here: `except Exception` does not see it and nothing is saved."""


def decisions_ticks():
    return runs_of("_decisions", "tick")


def closing_alerts(until=run_finished, cycles=400):
    """Cycle until the run ends, and three more: a finished run is left alone. Every alert seen."""
    alerts = []
    for _ in range(cycles):
        alerts += loop.cycle()[1]
        if until():
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"not reached: {state()['status']} {state()['closing']} {alerts}")
    for _ in range(3):
        alerts += loop.cycle()[1]
    return alerts


def on_default_branch(repo, name, make):
    make(repo / name)
    gitops.git(repo, "add", name)
    gitops.git(repo, "commit", "-q", "-m", f"plant {name}")
    gitops.git(repo, "push", "-q", "origin", "master")


def committed_decisions(repo, slug=SLUG):
    gitops.fetch(repo)
    return gitops.git(repo, "show", f"origin/ark/{slug}:docs/decisions/{slug}.md")


@pytest.mark.parametrize("raw", ['"# Decisions \\ud800 lone\\n"', '"# Decisions \\u0000 nul\\n"'])
def test_bytes_a_tick_cannot_store_are_cleaned_and_cost_one_tick(feature, monkeypatch, raw):
    monkeypatch.setenv("FAKE_DECISIONS", raw)
    repo, _ = feature()
    alerts = closing_alerts()
    st = state()
    assert st["status"] == "done" and decisions_ticks() == 1
    assert not [a for a in alerts if "runner error" in a]
    text = committed_decisions(repo)
    assert text.startswith("# Decisions") and "\0" not in text
    gitops.fetch(repo)
    numstat = gitops.git(repo, "diff", "--numstat", f"origin/ark/{SLUG}~1", f"origin/ark/{SLUG}")
    assert not numstat.startswith("-")                                      # text, not binary


def test_a_decisions_file_that_cannot_be_written_never_pays_a_second_tick(feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 3)
    repo, _ = feature()
    (paths.run_dir("demo", SLUG) / "decisions.md").mkdir(parents=True)       # no write can replace it
    alerts = closing_alerts()
    st = state()
    assert st["status"] == "blocked" and st["closing"] == "done" and st["final_pr"] is None
    assert decisions_ticks() == 1
    mine = [a for a in alerts if "IsADirectoryError" in a or "Is a directory" in a]
    assert len(mine) == 1 and "run blocked" in mine[0]
    assert not [a for a in alerts if "runner error" in a]


def test_a_tick_that_keeps_being_lost_is_tried_twice_then_the_fallback(feature, monkeypatch):
    def dies(c, r):
        raise Crash
    monkeypatch.setattr(loop, "on_decisions", dies)
    repo, _ = feature()
    for _ in range(400):
        try:
            loop.cycle()
        except Crash:
            pass
        if run_finished():
            break
        time.sleep(0.05)
    assert state()["status"] == "done" and decisions_ticks() == 2
    assert "written by the runner" in committed_decisions(repo)


def test_a_crash_right_after_starting_the_tick_adopts_it(feature, monkeypatch):
    real, crashed = loop.proc.start, []

    def start(run_dir, name, *a, **k):
        out = real(run_dir, name, *a, **k)
        if name == loop.DECISIONS and not crashed:
            crashed.append(name)
            raise Crash
        return out
    monkeypatch.setattr(loop.proc, "start", start)
    repo, _ = feature()
    for _ in range(400):
        try:
            loop.cycle()
        except Crash:
            pass
        if run_finished():
            break
        time.sleep(0.05)
    st = state()
    assert crashed and st["status"] == "done" and decisions_ticks() == 1
    assert not [e for e in st["events"] if "ignored the result" in e]
    assert not [e for e in st["events"] if "written by the runner" in e]


def test_a_tick_lost_once_starts_again_through_reconcile(feature, monkeypatch):
    real, crashed = loop.on_decisions, []

    def once(c, r):
        if not crashed:
            crashed.append(1)
            raise Crash
        real(c, r)
    monkeypatch.setattr(loop, "on_decisions", once)
    repo, _ = feature()
    for _ in range(400):
        try:
            loop.cycle()
        except Crash:
            pass
        if run_finished():
            break
        time.sleep(0.05)
    st = state()
    assert st["status"] == "done" and decisions_ticks() == 2
    assert any("gone without a result" in e for e in st["events"])
    assert "written by the runner" not in committed_decisions(repo)


def test_a_tick_that_cannot_start_leaves_the_fallback(feature, monkeypatch):
    real = loop.proc.start

    def start(run_dir, name, *a, **k):
        if name == loop.DECISIONS:
            raise OSError("no more processes")
        return real(run_dir, name, *a, **k)
    monkeypatch.setattr(loop.proc, "start", start)
    repo, _ = feature()
    alerts = closing_alerts()
    assert state()["status"] == "done" and decisions_ticks() == 0
    assert "written by the runner" in committed_decisions(repo)
    assert not [a for a in alerts if "runner error" in a]


def test_a_planted_decisions_symlink_overwrites_nothing(feature, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("safe\n")
    repo, _ = feature()
    planted = paths.run_dir("demo", SLUG) / "decisions.md"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.symlink_to(victim)
    closing_alerts()
    assert victim.read_text() == "safe\n"
    assert state()["status"] == "done" and not planted.is_symlink() and decisions_ticks() == 1
    assert committed_decisions(repo).startswith("# Decisions — fake")


def test_a_symlink_to_a_read_only_file_does_not_stop_the_close(feature, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("safe\n")
    victim.chmod(0o444)
    feature()
    planted = paths.run_dir("demo", SLUG) / "decisions.md"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.symlink_to(victim)
    closing_alerts()
    assert state()["status"] == "done" and victim.read_text() == "safe\n" and decisions_ticks() == 1


def test_a_symlink_planted_after_the_tick_is_not_published(feature, monkeypatch, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("# Decisions SECRET\n")
    real = loop.publish

    def planted_first(c):
        f = c.dir / "decisions.md"
        if not f.is_symlink() and not c.st.get("decisions_sha"):
            f.unlink()
            f.symlink_to(secret)
        real(c)
    monkeypatch.setattr(loop, "publish", planted_first)
    repo, _ = feature()
    closing_alerts()
    text = committed_decisions(repo)
    assert "SECRET" not in text and "written by the runner" in text


@pytest.mark.parametrize("name, make", [
    (".gitignore", lambda p: p.write_text("docs/\n")),                      # git add refuses an ignored path
    ("docs", lambda p: p.write_text("a file\n")),                           # not a directory
])
def test_a_branch_that_refuses_the_path_blocks_the_close_once(feature, name, make):
    repo, _ = feature()
    on_default_branch(repo, name, make)
    alerts = closing_alerts()
    st = state()
    assert st["status"] == "blocked" and st["closing"] == "done" and st["final_pr"] is None
    mine = [a for a in alerts if "run blocked" in a]
    assert len(mine) == 1 and "decisions.md" in mine[0]
    assert "decisions_sha" not in st and (paths.run_dir("demo", SLUG) / "decisions.md").is_file()


def fails(monkeypatch, target, name, error, when=lambda k: True):
    """Make target.name raise for the calls `when` picks by keywords; returns those calls, and undo() which
    brings the real one back. Only the close commits a file or opens a PR to the default branch, so a test
    sets this before the run: the close happens in the same cycle the last job's tick ends."""
    real, calls = getattr(target, name), []

    def broken(*a, **k):
        if not when(k):
            return real(*a, **k)
        calls.append(a)
        raise error
    monkeypatch.setattr(target, name, broken)
    return calls, lambda: monkeypatch.setattr(target, name, real)


def test_a_publish_that_keeps_failing_alerts_once_then_stops_at_the_limit(feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 3)
    monkeypatch.setattr(loop, "INFRA_LIMIT", 6)
    monkeypatch.setattr(loop, "BACKOFF_MAX", 0)
    calls, _ = fails(monkeypatch, gitops, "commit_file", gitops.GitError("git fetch: origin is gone"))
    repo, _ = feature()
    alerts = closing_alerts()
    st = state()
    assert len(calls) == 6 and st["status"] == "blocked" and st["closing"] == "done"
    waiting = [a for a in alerts if "still retrying" in a]
    stopped = [a for a in alerts if "run blocked" in a]
    assert len(waiting) == 1 and "origin is gone" in waiting[0]
    assert len(stopped) == 1 and "decisions.md" in stopped[0]
    assert "closing_errors" not in st and "closing_error" not in st


def test_a_publish_past_the_alert_waits_at_the_ceiling_and_can_still_heal(feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 3)
    calls, undo = fails(monkeypatch, gitops, "commit_file", gitops.GitError("offline"))
    repo, _ = feature()
    for _ in range(400):
        loop.cycle()
        if len(calls) >= 3:
            break
        time.sleep(0.05)
    for _ in range(5):
        loop.cycle()
    assert len(calls) == 3 and state()["status"] == "running"               # not every cycle any more
    undo()
    st = state()
    st["closing_wait"] = 0                                                  # the wait is over
    run.save(st)
    alerts = closing_alerts()
    st = state()
    assert st["status"] == "done" and st["final_pr"] and not [a for a in alerts if "run blocked" in a]
    assert not {"closing_errors", "closing_error", "closing_wait"} & set(st)


def test_a_final_pr_that_keeps_failing_says_the_file_is_on_the_branch(feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 2)
    monkeypatch.setattr(loop, "INFRA_LIMIT", 3)
    monkeypatch.setattr(loop, "BACKOFF_MAX", 0)
    calls, _ = fails(monkeypatch, forge.LocalForge, "create", forge.ForgeError("gh is down"),
                     when=lambda k: k["base"] == "master")
    repo, _ = feature()
    alerts = closing_alerts()
    st = state()
    assert st["status"] == "blocked" and st["decisions_sha"] and st["final_pr"] is None
    mine = [a for a in alerts if "run blocked" in a]
    assert len(mine) == 1 and f"ark/{SLUG}" in mine[0] and "gh is down" in mine[0]
    assert committed_decisions(repo).startswith("# Decisions")


def test_an_error_nobody_knows_to_heal_stops_the_close_at_the_alert_limit(feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 3)
    calls, _ = fails(monkeypatch, gitops, "commit_file", ValueError("what is this"))
    repo, _ = feature()
    alerts = closing_alerts()
    st = state()
    assert len(calls) == 3 and st["status"] == "blocked"
    mine = [a for a in alerts if "what is this" in a]
    assert len(mine) == 1 and "run blocked" in mine[0]
    assert not [a for a in alerts if "runner error" in a]


def test_the_word_symlink_in_a_passing_error_does_not_block_the_close(feature, monkeypatch):
    slug = "fix-symlink-race"
    repo, _ = feature(slug=slug)
    real, calls = gitops.commit_file, []

    def once(*a, **k):
        calls.append(a)
        if len(calls) == 1:
            raise gitops.GitError(f"git push origin HEAD:ark/{slug}: pre-push hook refused")
        return real(*a, **k)
    monkeypatch.setattr(gitops, "commit_file", once)
    closing_alerts(until=lambda: run_finished_of(slug))
    st = state(slug=slug)
    assert st["status"] == "done" and st["final_pr"] and len(calls) == 2


def run_finished_of(slug):
    st = state(slug=slug)
    return bool(st) and st["status"] in ("done", "blocked")


def test_a_new_approval_of_a_finished_run_is_not_silently_ignored(feature):
    repo, p = feature()
    closing_alerts()
    assert state()["status"] == "done"
    p.write_text(p.read_text() + "\nedited\n")
    approve(p, "demo", SLUG)
    alerts = []
    for _ in range(3):
        alerts += loop.cycle()[1]
    mine = [a for a in alerts if "already finished" in a]
    assert len(mine) == 1 and state()["status"] == "done"


# ---- main: the polling around cycle() -----------------------------------------------------------

def test_idle_cycle_and_drafts(runner, make_feature):
    runner()
    make_feature(plan_text(), create_repo=False)          # a draft: never picked up
    line, alerts, changed = loop.cycle()
    assert line == "idle" and alerts == [] and changed is False
    assert not paths.run_dir("demo", SLUG).exists()


def test_signature_changes_with_an_approval(runner, make_feature):
    runner()
    p = make_feature(plan_text(), create_repo=False)
    before = loop.signature()
    approve(p)
    assert loop.signature() != before


def test_signature_changes_when_an_approved_plan_is_edited(runner, make_feature):
    runner()
    p = make_feature(plan_text(), create_repo=False)
    approve(p)
    before = loop.signature()
    os.utime(p, (1, 1))  # an edit: the run has to stop on the stale approval without waiting for the tick
    assert loop.signature() != before


def test_signature_survives_a_broken_approval(ark_home):
    d = paths.approvals_dir() / "demo"
    d.mkdir(parents=True)
    (d / "gone.json").symlink_to(d / "nowhere")
    (d / "nul.json").write_text(json.dumps({"repo": "demo", "slug": "nul", "plan": "a\0b", "sha256": "x", "approved_at": "1"}))
    assert loop.signature() == loop.signature()


def test_signature_changes_with_what_a_process_needs_from_the_loop(runner, tmp_path):
    runner()
    d = paths.run_dir("demo", SLUG)
    info = proc.start(d, "x.worker", ["sleep", "30"], cwd=tmp_path, env=dict(os.environ), minutes=5)
    try:
        alive = loop.signature()
        assert loop.signature() == alive
        f = d / "x.worker.proc"
        good = f.read_text()
        f.write_text(json.dumps({**json.loads(good), "deadline": 1}))
        assert loop.signature() != alive                                        # past its deadline
        f.write_text("not json")
        assert loop.signature() != alive                                        # the cycle gives it a receipt
        f.write_text(good)
        assert loop.signature() == alive
        os.killpg(info["pid"], signal.SIGKILL)
        proc._children[str(d / "x.worker")].wait()
        assert loop.signature() != alive                                        # dead with no receipt
    finally:
        try:
            os.killpg(info["pid"], signal.SIGKILL)
        except OSError:
            pass
        proc._children.pop(str(d / "x.worker")).wait()


def test_signature_changes_with_a_receipt(ark_home):
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    before = loop.signature()
    (d / "x.worker.receipt.json").write_text("{}")
    assert loop.signature() != before


QUIET = ("idle", [], False)


class Polling:
    """loop.main with its sleep replaced: `each(n)` runs at the n-th poll, and the last poll is a Ctrl-C."""

    def __init__(self, monkeypatch):
        self.monkeypatch, self.cycles, self.sleeps = monkeypatch, 0, []

    def run(self, polls, cycle=lambda n: QUIET, each=lambda n: None, argv=()):
        self.cycles, self.sleeps = 0, []

        def counted(forge=None):
            self.cycles += 1
            return cycle(self.cycles)

        def sleep(seconds):
            self.sleeps.append(seconds)
            if len(self.sleeps) >= polls:
                raise KeyboardInterrupt
            each(len(self.sleeps))
        with self.monkeypatch.context() as m:
            m.setattr(loop, "cycle", counted)
            m.setattr(loop.time, "sleep", sleep)
            return loop.main(list(argv))


@pytest.fixture
def polling(ark_home, monkeypatch):
    return Polling(monkeypatch)


def set_workspace(ark_home, **over):
    f = ark_home / "workspace.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), **over}))


def running_run(ark_home, **job_fields):
    """A running run on disk with one job, without starting anything."""
    a = {"repo": "demo", "slug": SLUG, "plan": str(ark_home / "plan.md"), "sha256": "x", "approved_at": "1"}
    f = paths.approval_file("demo", SLUG)
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(a))
    st = run.new_state(a)
    st["status"] = "running"
    st["jobs"] = {"shell": {**run.new_job({"tier": "T1"}), "status": "needs_push", **job_fields}}
    run.save(st)
    return st


def test_a_quiet_loop_polls_without_cycling(polling, capsys):
    assert polling.run(4) == 130
    assert polling.cycles == 1 and polling.sleeps == [15, 15, 15, 15]
    assert capsys.readouterr().out.count("idle") == 1


def test_a_change_wakes_the_loop_before_the_tick(polling, runner, make_feature):
    runner()
    p = make_feature(plan_text(), create_repo=False)
    polling.run(4, each=lambda n: approve(p) if n == 2 else None)
    assert polling.cycles == 2


def test_the_tick_runs_a_cycle_with_nothing_changed(polling, ark_home):
    set_workspace(ark_home, tick_minutes=1e-9)
    polling.run(4)
    assert polling.cycles == 4


@pytest.mark.parametrize("fields", [{"errors": 1}, {"not_before": 1.0}])
def test_what_a_cycle_retries_gets_a_cycle_every_poll_and_one_pass(polling, ark_home, fields):
    """_error says "retried next cycle" and its alert comes after 20 of them, about 5 minutes: not 5 hours,
    and not 20 passes in a second, though each one changes the state."""
    running_run(ark_home, **fields)
    polling.run(4, cycle=lambda n: ("x", [], True))
    assert polling.cycles == 4


@pytest.mark.parametrize("fields", [
    {"errors": 1, "status": "merged"}, {"not_before": 1.0, "status": "blocked"},
    {"not_before": 1.0, "status": "working"}, {"not_before": 9e12}, {}])
def test_nothing_to_retry_leaves_the_loop_quiet(polling, ark_home, fields):
    running_run(ark_home, **fields)
    polling.run(4)
    assert polling.cycles == 1


def test_a_closing_retry_gets_a_cycle_when_its_wait_is_over(polling, ark_home):
    st = running_run(ark_home)
    st.update(closing_errors=1, closing_wait=9e12)
    run.save(st)
    polling.run(3)
    assert polling.cycles == 1
    st.update(closing_wait=1)
    run.save(st)
    polling.run(3)
    assert polling.cycles == 3


@pytest.mark.parametrize("status", ["held", "blocked", "done"])
def test_a_run_that_takes_no_step_has_nothing_to_retry(polling, ark_home, status):
    st = running_run(ark_home, errors=1)
    st["status"] = status
    run.save(st)
    polling.run(4)
    assert polling.cycles == 1


def test_a_run_still_moving_after_the_last_pass_goes_on_at_the_next_poll(polling):
    polling.run(4, cycle=lambda n: ("x", [], n < 8))
    assert polling.cycles == 8                                                  # 5 passes, then 3 until it settles


def test_a_broken_workspace_alerts_once_and_the_loop_stays_up(polling, ark_home, capsys):
    f = ark_home / "workspace.json"
    good = f.read_text()
    f.write_text("{not json")
    polling.run(5, each=lambda n: f.write_text(good) if n == 3 else None)
    out = capsys.readouterr().out
    assert out.count("⚠") == 1 and "JSONDecodeError" in out
    assert polling.cycles == 1 and "idle" in out                                # healed: it works again


def test_a_cycle_that_raises_does_not_end_the_loop(polling, capsys):
    def cycle(n):
        if n <= 2:
            raise forge.ForgeError("the same trouble")
        return QUIET
    assert polling.run(4, cycle=cycle) == 130
    out = capsys.readouterr().out
    assert out.count("the same trouble") == 1 and "idle" in out
    assert polling.cycles == 3


def test_once_reports_a_failed_cycle_in_its_exit_code(polling, ark_home, capsys):
    (ark_home / "workspace.json").write_text("{not json")
    assert polling.run(9, argv=["--once"]) == 1
    assert "⚠" in capsys.readouterr().out and polling.sleeps == []


@pytest.mark.parametrize("key", ["poll_seconds", "tick_minutes"])
@pytest.mark.parametrize("value", ["15", 0, -1, None, True, float("inf")])
def test_a_bad_timing_setting_alerts_and_never_spins(polling, ark_home, capsys, key, value):
    set_workspace(ark_home, **{key: value})
    assert polling.run(3) == 130
    out = capsys.readouterr().out
    assert out.count("⚠") == 1 and key in out
    assert polling.sleeps == [15, 15, 15] and polling.cycles == 0


def test_a_broken_workspace_loses_no_alert_of_the_cycle(feature, ark_home):
    feature()
    paths.run_dir("demo", SLUG).mkdir(parents=True)
    run.state_file("demo", SLUG).write_text("{damaged")
    good = (ark_home / "workspace.json").read_text()
    (ark_home / "workspace.json").write_text("{not json")
    with pytest.raises(ValueError):
        loop.cycle()
    (ark_home / "workspace.json").write_text(good)
    assert any("skipped until its state is repaired" in a for a in loop.cycle()[1])


def test_the_lock_is_free_again_after_main_returns(polling):
    assert polling.run(1, argv=["--once"]) == 0
    assert polling.run(1, argv=["--once"]) == 0


# ---- a stop from Marco wins over any retry ---------------------------------------------------------

def _stop_with_the_loop_racing(monkeypatch):
    """The loop's cycle comes in while `ark-dispatch stop` waits for the group to end."""
    real = proc._group_ended
    d = paths.run_dir("demo", SLUG)

    def racing(pgid, seconds):
        monkeypatch.setattr(proc, "_group_ended", real)
        end = time.monotonic() + 5
        while proc._leader_state(d, "shell.worker", proc._read(d / "shell.worker.proc")) == proc.OURS:
            assert time.monotonic() < end
            time.sleep(0.02)
        loop.cycle()
        return real(pgid, seconds)
    monkeypatch.setattr(proc, "_group_ended", racing)
    return proc.main(["stop", f"demo/{SLUG}", "shell"])


def _settle():
    for _ in range(5):
        loop.cycle()


def _one_worker_ever():
    """The fake counts a worker only when it finishes: these never do. A retry would be running, or archived."""
    d = paths.run_dir("demo", SLUG)
    return "shell.worker" not in proc.active(d) and len(list((d / "done").glob("shell.worker-*.json"))) == 1


def test_a_stop_that_the_loop_reaps_first_blocks_the_job_and_never_retries(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    feature()
    loop.cycle()
    assert _stop_with_the_loop_racing(monkeypatch) == 0
    _settle()
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and j["reason"] == "stopped by Marco" and j["tier_attempts"] == 0
    assert _one_worker_ever()


def test_a_crash_right_after_consuming_a_stop_does_not_forget_it(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    feature()
    loop.cycle()
    assert proc.main(["stop", f"demo/{SLUG}", "shell"]) == 0
    with monkeypatch.context() as m:
        m.setattr(loop, "_on_receipt", lambda c, name, r: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            loop.cycle()
    assert state()["jobs"]["shell"]["status"] == "working" and not proc.active(paths.run_dir("demo", SLUG))
    _settle()
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and j["reason"] == "stopped by Marco"
    assert _one_worker_ever()


def test_a_save_that_fails_right_after_consuming_a_stop_does_not_forget_it(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    feature()
    loop.cycle()
    assert proc.main(["stop", f"demo/{SLUG}", "shell"]) == 0
    with monkeypatch.context() as m:
        m.setattr(run, "save", lambda st: (_ for _ in ()).throw(OSError("read-only")))
        loop.cycle()
    assert state()["jobs"]["shell"]["status"] == "working"
    _settle()
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and j["reason"] == "stopped by Marco"
    assert _one_worker_ever()


def test_a_stop_blocks_even_a_job_whose_status_moved_on(tmp_path, monkeypatch):
    c = bare(tmp_path, status="needs_gate")
    consume_one(monkeypatch, c, "shell.worker", receipt(exit=-15, reason="stopped by Marco"))
    assert c.st["jobs"]["shell"]["status"] == "blocked"


def test_a_stop_does_not_unmerge_a_job(tmp_path, monkeypatch):
    c = bare(tmp_path, status="merged")
    consume_one(monkeypatch, c, "shell.worker", receipt(exit=-15, reason="stopped by Marco"))
    assert c.st["jobs"]["shell"]["status"] == "merged"


# ---- a FIFO in the run folder stops nothing ----------------------------------------------------------

class Hung(BaseException):
    """Not an Exception: the loop's guards catch those and go on."""


@pytest.fixture
def must_not_hang():
    def alarm(*_):
        raise Hung()
    before = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, 90)
    yield
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, before)


@pytest.mark.parametrize("name", ["evil.proc", "link.proc", "shell.worker.receipt.json", "state.json", "merge.lock"])
def test_a_fifo_in_a_run_folder_hangs_nothing_and_the_other_runs_go_on(feature, monkeypatch, must_not_hang, name):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    feature()
    feature(repo="other", slug="fine")
    loop.cycle()
    d = paths.run_dir("demo", SLUG)
    if name == "link.proc":
        os.mkfifo(d / "pipe")
        (d / name).symlink_to(d / "pipe")
    else:
        (d / name).unlink(missing_ok=True)
        os.mkfifo(d / name)
    loop.signature()
    loop._retry_due()
    drive(lambda: jobs_settled("other", "fine"))
    assert state("other", "fine")["jobs"]["shell"]["status"] == "merged"


def test_a_fifo_as_state_tells_marco_once(feature, must_not_hang):
    feature()
    loop.cycle()
    f = run.state_file("demo", SLUG)
    f.unlink()
    os.mkfifo(f)
    seen = [a for _ in range(3) for a in loop.cycle()[1] if a.startswith(f"demo/{SLUG}:")]
    assert len(seen) == 1 and "skipped" in seen[0]


# -- across runs

def test_writer_limit_holds_across_repos(feature, ark_home, tmp_path, monkeypatch):
    release = tmp_path / "release"
    monkeypatch.setenv("FAKE_HOLD", str(release))  # a worker lives until we have seen the limit bite
    limits(ark_home, writers_total=1)
    one = manifest(jobs=[manifest()["jobs"][0]])
    feature(one, repo="demo", tasks=("T1", "T2"))                 # T3 belongs to no job here
    feature(one, repo="other", tasks=("T1", "T2"))

    def held_back():  # one repo's worker is alive, the other's run is running with its job still pending
        sts = [state(r) for r in ("demo", "other")]
        if not all(sts):
            return False
        jobs = sorted(st["jobs"]["shell"]["status"] for st in sts)
        return jobs == ["pending", "working"] and all(st["status"] == "running" for st in sts)

    try:
        drive(held_back)
        for _ in range(5):  # and it stays held while the first worker lives
            loop.cycle()
            assert sorted(state(r)["jobs"]["shell"]["status"] for r in ("demo", "other")) == ["pending", "working"]
            time.sleep(0.05)
    finally:
        release.touch()
    drive(lambda: jobs_settled("demo") and jobs_settled("other"))
    assert state("demo")["jobs"]["shell"]["status"] == "merged"
    assert state("other")["jobs"]["shell"]["status"] == "merged"
    assert (tmp_path / "fake" / "max_active").read_text() == "1"


def test_overlapping_plans_in_one_repo_wait(feature, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "0.3")
    feature()
    p2 = make_feature(plan_text(slug="notes-more"), slug="notes-more", create_repo=False)
    approve(p2, slug="notes-more", at="2026-11-05T11:00:00+00:00")
    loop.cycle()
    assert state()["status"] == "running"
    second = state(slug="notes-more")
    assert second["status"] == "waiting" and "overlaps the running plan notes-edit" in second["reason"]
    drive(run_finished)
    prs_file = paths.find_repo("demo") / ".git" / "ark-local-prs.json"  # its final PR merged: the wait ends
    prs_file.write_text(json.dumps([dict(pr, state="MERGED") if pr["url"] == state()["final_pr"]["url"] else pr
                                    for pr in json.loads(prs_file.read_text())]))
    drive(lambda: state(slug="notes-more")["status"] in ("done", "blocked"))
    assert state(slug="notes-more")["status"] == "done"


def test_changed_plan_holds_the_run(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    repo, plan = feature()
    loop.cycle()
    assert state()["jobs"]["shell"]["status"] == "working"
    plan.write_text(plan.read_text() + "\nedited after approval\n")
    line, alerts, _ = loop.cycle()
    assert state()["status"] == "held" and "held" in line
    assert any("changed after its approval" in a for a in alerts)
    assert "Status: held" in paths.run_file("demo", SLUG).read_text()
    d = paths.run_dir("demo", SLUG)
    for _ in range(600):  # the worker finishes whenever it finishes; the held run must not consume it
        loop.cycle()
        if proc.finished(d) == ["shell.worker"]:
            break
        time.sleep(0.05)
    assert proc.finished(d) == ["shell.worker"]
    loop.cycle()
    st = state()
    assert st["status"] == "held" and st["jobs"]["shell"]["status"] == "working"   # receipt waits
    assert gitops.remote_sha(repo, f"ark/{SLUG}-shell") is None
    assert proc.finished(d) == ["shell.worker"]


# -- branch names across runs: ark/<slug>-<job> of one run can be a branch of another

def plan_in(make_feature, slug, *jobs, repo="demo", at="2026-11-05T11:00:00+00:00"):
    """Approve one more plan in a repo that already exists."""
    tasks = [f"T{i + 1}" for i in range(len(jobs))]
    m = manifest(jobs=[job(j, [t], [f"src/{slug}-{j}/**"]) for j, t in zip(jobs, tasks)])
    p = make_feature(plan_text(m, tasks=tasks, repo=repo, slug=slug), repo=repo, slug=slug, create_repo=False)
    approve(p, repo, slug, at=at)
    return p


def first_plan(feature, slug, *jobs, repo="demo"):
    tasks = tuple(f"T{i + 1}" for i in range(len(jobs)))
    m = manifest(jobs=[job(j, [t], [f"src/{slug}-{j}/**"]) for j, t in zip(jobs, tasks)])
    return feature(m, repo=repo, slug=slug, tasks=tasks)


@pytest.fixture
def held(tmp_path, monkeypatch):
    """Workers live until the test releases them; released in any case, so none outlives the test."""
    release = tmp_path / "release"
    monkeypatch.setenv("FAKE_HOLD", str(release))
    yield release
    release.touch()


def cycles_alerts(n=3):
    return [a for _ in range(n) for a in loop.cycle()[1]]


def test_a_job_branch_of_a_live_run_is_never_the_integration_branch_of_another(feature, make_feature, held):
    """The final review's reproduction: foo's worker is alive when foo-bar is approved."""
    repo, _ = first_plan(feature, "foo", "bar")
    drive(lambda: state(slug="foo")["jobs"]["bar"]["status"] == "working")
    plan_in(make_feature, "foo-bar", "z")
    alerts = cycles_alerts()
    second = state(slug="foo-bar")
    assert second["status"] == "waiting" and not second["jobs"]
    assert gitops.remote_sha(repo, "ark/foo-bar") is None                       # it wrote nothing
    mine = [a for a in alerts if a.startswith("demo/foo-bar:")]
    assert len(mine) == 1 and "ark/foo-bar" in mine[0] and "demo/foo" in mine[0] and "job bar" in mine[0]
    held.touch()
    drive(lambda: state(slug="foo")["status"] in ("done", "blocked"))
    assert state(slug="foo")["status"] == "done"
    second = state(slug="foo-bar")                                              # its name is still taken on origin
    assert second["status"] == "waiting" and "already exists" in second["reason"]
    gitops.fetch(repo)
    log = gitops.git(repo, "log", "--format=%s", "origin/ark/foo-bar").splitlines()
    assert "feat: z" not in log and not [p for p in forge.LocalForge().list(repo) if p["base"] == "ark/foo-bar"]


def test_the_collision_also_stops_the_run_whose_job_branch_is_the_live_integration_branch(feature, make_feature, held):
    repo, _ = first_plan(feature, "foo-bar", "z")
    drive(lambda: state(slug="foo-bar")["jobs"]["z"]["status"] == "working")
    plan_in(make_feature, "foo", "bar")
    alerts = cycles_alerts()
    st = state(slug="foo")
    assert st["status"] == "waiting" and gitops.remote_sha(repo, "ark/foo") is None
    mine = [a for a in alerts if a.startswith("demo/foo:")]
    assert len(mine) == 1 and "ark/foo-bar" in mine[0] and "demo/foo-bar" in mine[0]


def test_two_job_branches_collide_even_with_neither_on_origin(feature, make_feature, held):
    repo, _ = first_plan(feature, "foo", "bar-baz")
    drive(lambda: state(slug="foo")["jobs"]["bar-baz"]["status"] == "working")
    plan_in(make_feature, "foo-bar", "baz")
    alerts = cycles_alerts()
    assert gitops.remote_sha(repo, "ark/foo-bar-baz") is None
    st = state(slug="foo-bar")
    assert st["status"] == "waiting" and "ark/foo-bar-baz" in st["reason"]
    assert gitops.remote_sha(repo, "ark/foo-bar") is None
    assert sum("ark/foo-bar-baz" in a for a in alerts) == 1


def test_a_collision_is_heard_even_when_the_areas_overlap_too(feature, make_feature, held):
    repo, _ = first_plan(feature, "foo", "bar")
    drive(lambda: state(slug="foo")["jobs"]["bar"]["status"] == "working")
    m = manifest(jobs=[job("z", ["T1"], ["src/foo-bar/**"])])                   # the same area as foo's job
    p = make_feature(plan_text(m, tasks=("T1",), slug="foo-bar"), slug="foo-bar", create_repo=False)
    approve(p, "demo", "foo-bar", at="2026-11-05T11:00:00+00:00")
    alerts = cycles_alerts()
    assert sum(a.startswith("demo/foo-bar:") and "ark/foo-bar" in a for a in alerts) == 1


def test_a_held_run_keeps_its_branch_names(feature, make_feature, held):
    repo, plan = first_plan(feature, "foo", "bar")
    drive(lambda: state(slug="foo")["jobs"]["bar"]["status"] == "working")
    plan.write_text(plan.read_text() + "\nedited after approval\n")
    loop.cycle()
    assert state(slug="foo")["status"] == "held"
    plan_in(make_feature, "foo-bar", "z")
    loop.cycle()
    assert state(slug="foo-bar")["status"] == "waiting" and "held plan demo/foo" in state(slug="foo-bar")["reason"]
    assert gitops.remote_sha(repo, "ark/foo-bar") is None


def test_a_live_run_with_no_approval_keeps_its_branch_names(feature, make_feature, held):
    """It takes no step now, but it goes on when the approval is back."""
    repo, _ = first_plan(feature, "foo", "bar")
    drive(lambda: state(slug="foo")["jobs"]["bar"]["status"] == "working")
    paths.approval_file("demo", "foo").unlink()
    plan_in(make_feature, "foo-bar", "z")
    loop.cycle()
    assert state(slug="foo-bar")["status"] == "waiting" and "demo/foo" in state(slug="foo-bar")["reason"]
    assert gitops.remote_sha(repo, "ark/foo-bar") is None


def test_a_run_with_an_unreadable_state_keeps_every_branch_under_its_slug(feature, make_feature, held):
    """Its jobs cannot be read, so nothing that could be one of its branches starts. Other names do."""
    repo, _ = first_plan(feature, "foo", "bar")
    drive(lambda: state(slug="foo")["jobs"]["bar"]["status"] == "working")
    run.state_file("demo", "foo").write_text("{ not json")
    plan_in(make_feature, "foo-bar", "z")
    plan_in(make_feature, "other", "z", at="2026-11-05T12:00:00+00:00")
    alerts = cycles_alerts()
    st = state(slug="foo-bar")
    assert st["status"] == "waiting" and "ark/foo-bar" in st["reason"] and "demo/foo" in st["reason"]
    assert gitops.remote_sha(repo, "ark/foo-bar") is None
    assert state(slug="other")["status"] == "running"
    assert sum(a.startswith("demo/foo-bar:") for a in alerts) == 1


def test_the_same_names_in_another_repo_are_no_collision(feature, make_feature, held):
    first_plan(feature, "foo", "bar")
    other, _ = first_plan(feature, "foo-bar", "z", repo="other")
    loop.cycle()
    assert state(slug="foo")["status"] == "running" and state("other", "foo-bar")["status"] == "running"


def test_two_checkouts_of_one_origin_are_one_repo_for_branch_names(feature, make_feature, tmp_path, held):
    repo, _ = first_plan(feature, "foo", "bar")
    drive(lambda: state(slug="foo")["jobs"]["bar"]["status"] == "working")
    clone = tmp_path / "ws" / "clone"
    subprocess.run(["git", "clone", "-q", gitops.origin_url(repo), str(clone)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "set-head", "origin", "master"], cwd=clone, check=True, capture_output=True)
    plan_in(make_feature, "foo-bar", "z", repo="clone")
    loop.cycle()
    st = state("clone", "foo-bar")
    assert st["status"] == "waiting" and "demo/foo" in st["reason"]
    assert gitops.remote_sha(repo, "ark/foo-bar") is None


def test_a_finished_run_gives_its_names_back(feature, make_feature):
    repo, _ = first_plan(feature, "foo", "bar")
    drive(lambda: state(slug="foo")["status"] == "done")
    plan_in(make_feature, "foo-bar", "z")
    loop.cycle()
    assert "already exists" in state(slug="foo-bar")["reason"]                  # origin still has it: as before
    gitops.git(repo, "push", "-q", "origin", "--delete", "ark/foo-bar")
    loop.cycle()
    assert state(slug="foo-bar")["status"] == "running"


# -- a lost wake-up: what lands during a cycle that changed nothing

def test_a_receipt_written_during_an_idle_cycle_wakes_the_next_poll(ark_home, monkeypatch):
    class Stop(BaseException):
        pass
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "exclude": [], "tick_minutes": 60, "poll_seconds": 1}))
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    cycles, polls = [], []

    def idle_cycle():
        cycles.append(1)
        if len(cycles) == 1:  # the tail of the pass, after the receipts were looked at
            (d / "shell.worker.receipt.json").write_text("{}")
        return "idle", [], False

    def poll(_):
        polls.append(1)
        if len(polls) == 4:
            raise Stop
    monkeypatch.setattr(loop, "cycle", idle_cycle)
    monkeypatch.setattr(loop.time, "sleep", poll)
    with pytest.raises(Stop):
        loop._polling(False)
    assert len(cycles) == 2                                                     # woken once, then quiet again


# -- what is planted where a receipt must be archived stalls nothing for ever

def test_a_file_at_done_goes_to_euklides_and_finishes_the_run(feature, make_feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 3)
    feature(manifest(jobs=[job("shell", ["T1"], ["src/shell/**"])]), tasks=("T1",))
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    (d / "done").write_text("planted")
    drive(stopping(lambda: state()["status"] in ("done", "blocked")))
    st = state()
    alerts = [e for e in st["events"] if "ALERT" in e]
    assert st["status"] == "blocked" and st["jobs"]["shell"]["status"] == "blocked"
    assert "stopped by Marco" in st["jobs"]["shell"]["reason"]
    assert any(str(d / "done") in e for e in st["events"])                      # Euklides was told what could not be archived
    assert not [a for a in alerts if "runner error" in a]
    assert not (d / "shell.worker.proc").exists()


def test_a_job_that_cannot_be_archived_does_not_hold_back_the_others(feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 1000)
    jobs = [job("a", ["T1"], ["src/a/**"]), job("b", ["T2"], ["src/b/**"])]
    feature(manifest(jobs=jobs), tasks=("T1", "T2"))
    loop.cycle()
    d = paths.run_dir("demo", SLUG)
    real = proc.consume

    def refuses_a(run_dir, name):
        if name.startswith("a."):
            raise IsADirectoryError(f"planted at {name}")
        return real(run_dir, name)
    monkeypatch.setattr(proc, "consume", refuses_a)
    drive(lambda: state()["jobs"]["b"]["status"] == "merged" and proc.finished(d) == ["a.worker"])
    assert state()["jobs"]["a"]["status"] == "working" and state()["jobs"]["a"]["errors"]
    assert loop._retry_due()                                                    # its limit counts cycles: no waiting for the tick


def test_removing_what_was_planted_heals_the_job(feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 1000)
    feature(manifest(jobs=[job("shell", ["T1"], ["src/shell/**"])]), tasks=("T1",))
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    (d / "done").write_text("planted")
    drive(lambda: (loop.cycle(), state()["jobs"]["shell"].get("errors"))[1])
    (d / "done").unlink()
    drive(lambda: state()["status"] in ("done", "blocked"))
    assert state()["status"] == "done"
    assert "errors" not in state()["jobs"]["shell"] and "error" not in state()["jobs"]["shell"]


def test_a_directory_at_a_proc_goes_to_euklides_and_finishes_the_run(feature, monkeypatch, held):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 3)
    feature(manifest(jobs=[job("shell", ["T1"], ["src/shell/**"])]), tasks=("T1",))
    drive(lambda: state()["jobs"]["shell"]["status"] == "working")
    d = paths.run_dir("demo", SLUG)
    (d / "shell.worker.proc").unlink()
    (d / "shell.worker.proc").mkdir()
    (d / "shell.worker.proc" / "inside").write_text("planted")
    held.touch()
    drive(stopping(lambda: state()["status"] in ("done", "blocked")))
    st = state()
    assert st["status"] == "blocked" and st["jobs"]["shell"]["status"] == "blocked"
    assert "stopped by Marco" in st["jobs"]["shell"]["reason"]
    assert not os.path.lexists(d / "shell.worker.proc") and not proc.active(d)


def test_the_decisions_tick_that_cannot_be_archived_stops_the_close(feature, monkeypatch):
    monkeypatch.setattr(loop, "ERROR_ALERT_AFTER", 3)
    feature(manifest(jobs=[job("shell", ["T1"], ["src/shell/**"])]), tasks=("T1",))
    real = proc.consume

    def refuses_the_tick(run_dir, name):
        if name == loop.DECISIONS:
            raise FileExistsError(f"planted, {name}")
        return real(run_dir, name)
    monkeypatch.setattr(proc, "consume", refuses_the_tick)
    drive(lambda: state()["status"] in ("done", "blocked"))
    assert state()["status"] == "blocked" and state()["closing"] == "done"
    alerts = [e for e in state()["events"] if "ALERT" in e]
    assert sum("run blocked" in a and "FileExistsError" in a for a in alerts) == 1
    assert not [a for a in alerts if "runner error" in a]


def test_a_planted_name_of_no_job_goes_at_once(feature):
    feature(manifest(jobs=[job("shell", ["T1"], ["src/shell/**"])]), tasks=("T1",))
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    (d / "evil.worker.proc").mkdir()
    drive(lambda: state()["status"] in ("done", "blocked"))
    assert state()["status"] == "done" and not os.path.lexists(d / "evil.worker.proc")


# -- Euklides: what goes to it

def test_a_failure_goes_to_euklides(tmp_path):
    c = bare(tmp_path)
    loop.fail(c, "shell", "tests are red")
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and j["failure"] == "tests are red"
    assert j["euklides"] == {"trigger": "failure", "situation": "tests are red", "kind": None}


def test_a_blocked_worker_question_goes_to_euklides(tmp_path, monkeypatch):
    c = bare(tmp_path)
    monkeypatch.setattr(loop, "_clean_exit", lambda r: True)
    loop.on_worker(c, "shell", receipt("need it\nSTATUS: BLOCKED — which store?"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and j["euklides"]["trigger"] == "question"
    assert j["euklides"]["situation"] == j["question"] == "which store?"


def test_a_reviewer_without_verdict_twice_goes_to_euklides(tmp_path):
    c = bare(tmp_path, status="reviewing", review_errors=1)
    loop.on_review(c, "shell", receipt("no idea"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and j["euklides"]["trigger"] == "blocked"
    assert j["euklides"]["kind"] == "review" and "no verdict twice" in j["euklides"]["situation"]


def test_a_check_lost_again_and_again_goes_to_euklides_not_to_a_block(tmp_path):
    c = bare(tmp_path, status="integrating", integrate_lost=5)
    loop.on_integrate(c, "shell", receipt(reason="timeout"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_euklides" and j["euklides"]["back"] == "needs_integrate"


def test_start_euklides_runs_with_its_hands_in_the_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(gitops, "origin_url", lambda repo: "git@example.com:o/r.git")
    c = bare(tmp_path, status="needs_euklides", euklides={"trigger": "failure", "situation": "red", "kind": None})
    (tmp_path / "shell").mkdir()
    c.plan = SimpleNamespace(manifest={"jobs": [c.mjobs["shell"], job("ui", ["T2"], ["src/ui/**"], after=["shell"])]},
                             sections={"Goal": "g"})
    c.read_report = lambda j: "report"
    started = {}
    monkeypatch.setattr(proc, "start", lambda d, name, cmd, **kw: started.update(name=name, cmd=cmd, **kw))
    monkeypatch.setattr(loop.brief, "task_texts", lambda plan: {}, raising=False)
    loop.start_euklides(c, "shell")
    j = c.st["jobs"]["shell"]
    assert j["status"] == "euklides" and j["euklides"]["allowed"] == ["retry", "escalate", "fixed", "ask"]
    assert started["name"] == "shell.euklides" and started["cwd"] == tmp_path / "shell"
    assert started["minutes"] == 30 and started["env"]["ARK_ROLE"] == "euklides"
    assert started["cmd"][started["cmd"].index("--allowedTools") + 1] == "Read,Grep,Glob,Bash,Edit,Write"
    env = started["env"]
    n = int(env["GIT_CONFIG_COUNT"]) - 1  # the last entry: the one runner_env added
    assert env[f"GIT_CONFIG_KEY_{n}"] == f"url.{claude.DEAD_PUSH_URL}.pushInsteadOf"
    assert env[f"GIT_CONFIG_VALUE_{n}"] == "git@example.com:o/r.git"
    assert "(no commits)" in started["cmd"][started["cmd"].index("-p") + 1]  # not a repo: empty lists
    assert "Jobs that build on this one: ui" in started["cmd"][started["cmd"].index("-p") + 1]


def test_start_euklides_takes_its_settings_from_the_workspace(tmp_path, monkeypatch, ark_home):
    monkeypatch.setattr(gitops, "origin_url", lambda repo: "git@example.com:o/r.git")
    set_workspace(ark_home, euklides={"model": "sonnet", "usd_per_call": 2, "minutes_per_call": 7, "tools": ["Read"]})
    c = bare(tmp_path, status="needs_euklides", euklides={"trigger": "failure", "situation": "red", "kind": None})
    c.plan = SimpleNamespace(manifest={"jobs": [c.mjobs["shell"]]}, sections={"Goal": "g"})
    c.read_report = lambda j: "report"
    started = {}
    monkeypatch.setattr(proc, "start", lambda d, name, cmd, **kw: started.update(cmd=cmd, **kw))
    monkeypatch.setattr(loop.brief, "task_texts", lambda plan: {}, raising=False)
    loop.start_euklides(c, "shell")
    cmd = started["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[cmd.index("--max-budget-usd") + 1] == "2"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read" and started["minutes"] == 7


def test_a_bad_euklides_setting_stops_the_cycle_before_any_process(ark_home, monkeypatch):
    set_workspace(ark_home, euklides={"model": 5})
    monkeypatch.setattr(proc, "start", lambda *a, **k: pytest.fail("a process started"))
    with pytest.raises(ValueError, match="euklides.model"):
        loop.cycle(SimpleNamespace())


def test_start_euklides_without_a_worktree_runs_in_the_ticks_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(gitops, "origin_url", lambda repo: "git@example.com:o/r.git")
    c = bare(tmp_path, status="needs_euklides", euklides={"trigger": "question", "situation": "q", "kind": None})
    c.plan = SimpleNamespace(manifest={"jobs": [c.mjobs["shell"]]}, sections={"Goal": "g"})
    c.read_report = lambda j: None
    started = {}
    monkeypatch.setattr(proc, "start", lambda d, name, cmd, **kw: started.update(**kw))
    monkeypatch.setattr(loop.brief, "task_texts", lambda plan: {}, raising=False)
    loop.start_euklides(c, "shell")
    assert started["cwd"] == loop.ticks_dir()


# -- Euklides: what its answer does

def at_euklides(tmp_path, trigger="failure", allowed=("retry", "escalate", "ask"), kind=None, situation="red", **over):
    return bare(tmp_path, status="euklides", failure=situation if trigger == "failure" else None,
                question=situation if trigger == "question" else None,
                euklides={"trigger": trigger, "situation": situation, "kind": kind, "allowed": list(allowed)}, **over)


def euk_answer(**d):
    return receipt(json.dumps(d), usd=0.4)


def test_a_retry_resumes_the_session_with_the_note_and_is_journaled(tmp_path, ark_home):
    c = at_euklides(tmp_path, session="s1")
    loop.on_euklides(c, "shell", euk_answer(action="retry", note="fix the import"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and "fix the import" in j["resume_msg"] and "euklides" not in j
    assert j["euklides_count"] == 1
    [row] = euklides_mod.decisions()
    assert row["action"] == "retry" and row["valid"] is True and row["usd"] == 0.4 and row["trigger"] == "failure"


def test_an_escalation_starts_fresh_one_tier_up(tmp_path, ark_home):
    c = at_euklides(tmp_path, session="s1")
    loop.on_euklides(c, "shell", euk_answer(action="escalate", note="harder than it looks"))
    j = c.st["jobs"]["shell"]
    assert j["tier"] == "T2" and j["session"] is None and j["resume_msg"] is None
    assert "harder than it looks" in j["note"] and j["status"] == "needs_worker"


def test_an_answer_goes_back_to_the_worker(tmp_path, ark_home):
    c = at_euklides(tmp_path, trigger="question", allowed=("answer", "ask"), situation="which store?", session="s1")
    loop.on_euklides(c, "shell", euk_answer(action="answer", note="SQLite", undo="job", why="local"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and "which store?" in j["resume_msg"] and "SQLite" in j["resume_msg"]
    assert "question" not in j and not c.st.get("questions")


def test_an_answer_that_reaches_the_plan_asks_marco_with_it_as_proposal(tmp_path, ark_home):
    c = at_euklides(tmp_path, trigger="question", allowed=("answer", "ask"), situation="rename the column?")
    loop.on_euklides(c, "shell", euk_answer(action="answer", note="yes, to user_id", undo="plan", why="ui reads it"))
    j = c.st["jobs"]["shell"]
    [q] = c.st["questions"]
    assert j["status"] == "waiting_answer" and "Proposal: yes, to user_id" in q["text"]
    assert j.get("euklides_count", 0) == 0
    [row] = euklides_mod.decisions()
    assert row["action"] == "ask" and row["undo"] == "plan"


@pytest.mark.parametrize("r", [receipt("no json"), receipt('{"action": "block", "note": "x"}'),
                               receipt('{"action": "retry", "note": "x"}', reason="timeout"),
                               receipt('{"action": "retry", "note": "x"}', exit=1)])
def test_an_unusable_answer_asks_marco_and_is_journaled_invalid(tmp_path, ark_home, r):
    c = at_euklides(tmp_path)
    loop.on_euklides(c, "shell", r)
    assert c.st["jobs"]["shell"]["status"] == "waiting_answer" and "red" in c.st["questions"][0]["text"]
    assert euklides_mod.decisions()[0]["valid"] is False


def test_unblock_after_the_integration_gate_failed_twice_resets_the_count(tmp_path, ark_home):
    c = at_euklides(tmp_path, trigger="blocked", allowed=("unblock", "ask"), kind="integrate",
                    situation="integration gate failed twice: red", integrate_fails=2, session="s1")
    loop.on_euklides(c, "shell", euk_answer(action="unblock", note="merge the base first"))
    j = c.st["jobs"]["shell"]
    assert j["integrate_fails"] == 0 and j["status"] == "needs_worker"
    assert "git merge origin/ark/" in j["resume_msg"] and "merge the base first" in j["resume_msg"]


def test_unblock_after_two_reviews_without_verdict_reviews_again(tmp_path, ark_home):
    c = at_euklides(tmp_path, trigger="blocked", allowed=("unblock", "ask"), kind="review",
                    situation="the reviewer gave no verdict twice", review_errors=2)
    loop.on_euklides(c, "shell", euk_answer(action="unblock", note="review again"))
    j = c.st["jobs"]["shell"]
    assert j["review_errors"] == 0 and j["status"] == "needs_review"


def asked(tmp_path, proposal=":stop — widen the area to src/db/**"):
    c = at_euklides(tmp_path, trigger="blocked", allowed=("unblock", "ask"), kind="area",
                    situation="changed files outside its area: src/db/x.py", session="s1")
    loop.on_euklides(c, "shell", euk_answer(action="ask", note="widen the area?", proposal=proposal))
    return c


def test_ok_to_a_stop_proposal_blocks_with_it_and_journals_marco(tmp_path, ark_home):
    c = asked(tmp_path)
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    j = c.st["jobs"]["shell"]
    assert j["status"] == "blocked" and "widen the area to src/db/**" in j["reason"]
    assert euklides_mod.decisions()[0]["marco"] == "ok"


def test_ok_to_a_proposal_makes_it_the_answer(tmp_path, ark_home):
    c = asked(tmp_path, proposal="revert src/db/x.py and keep the change inside src/shell")
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and "revert src/db/x.py" in j["resume_msg"]


def test_any_other_answer_is_the_answer(tmp_path, ark_home):
    c = asked(tmp_path)
    loop._apply_answer(c.st, {"id": "q1", "text": "move it to src/shell/db.py"})
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and "move it to src/shell/db.py" in j["resume_msg"]
    assert euklides_mod.decisions()[0]["marco"] == "move it to src/shell/db.py"


def test_a_journal_that_cannot_be_written_does_not_stop_the_decision(tmp_path, ark_home, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(euklides_mod, "record", broken)
    c = at_euklides(tmp_path, session="s1")
    loop.on_euklides(c, "shell", euk_answer(action="retry", note="again"))
    assert c.st["jobs"]["shell"]["status"] == "needs_worker"
    assert any("journal" in e for e in c.st["events"])


def test_a_new_approval_that_only_widens_the_area_keeps_the_merged_jobs(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    _, p = feature()
    drive(lambda: state()["jobs"]["shell"]["status"] == "merged")
    shell_area = [g for g in state()["area"] if "shell" in g]
    _hold_and_edit(p)
    loop.cycle()
    assert state()["status"] == "held"
    _reapproved_with(p, manifest(jobs=[job("shell", ["T1", "T2"], shell_area),
                                       job("ui", ["T3"], ["src/ui/**", "extra/**"], after=["shell"])]))
    alerts = turn(3)
    st = state()
    assert not [a for a in alerts if "cannot take the new approval" in a]
    assert st["status"] == "running" and st["plan_sha"] == sha256_file(p) and "extra/**" in st["area"]
    assert st["jobs"]["shell"]["status"] == "merged"


# -- Euklides repairs the branch himself: `fixed`

FIX_INTO_AREA = ("git reset -q --hard HEAD~1 && mkdir -p src/shell && echo fixed > src/shell/shell.txt "
                 "&& git add -A && git commit -q -m 'shell: moved into the area'")


def test_a_fixed_branch_is_pushed_and_gated_without_a_new_worker(feature, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    monkeypatch.setenv("FAKE_EUKLIDES_RUN", FIX_INTO_AREA)
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "fixed", "note": "moved the file into the area"}')
    repo, _ = feature()
    drive(jobs_settled)
    st = state()
    j = st["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 1 and j["euklides_count"] == 1
    assert (tmp_path / "fake" / "shell.worker.count").read_text() == "1"      # no new worker attempt
    assert [r["action"] for r in euklides_mod.decisions()] == ["fixed"]
    assert "rewrite_ok" not in j
    fixed = next(i for i, e in enumerate(st["events"]) if "shell euklides: fixed" in e)
    pushed = next(i for i, e in enumerate(st["events"]) if "shell: head" in e and "pushed" in e)
    gate = next(i for i, e in enumerate(st["events"]) if "shell: job gate green" in e)
    assert fixed < pushed < gate
    gitops.fetch(repo)
    assert "outside/shell.txt" not in gitops.changed_files(repo, st["base_sha"], f"origin/ark/{SLUG}")


@pytest.mark.parametrize("cmd, why", [
    ("echo x > loose.txt", "uncommitted changes"),
    ("git checkout -q -b elsewhere", f"not ark/{SLUG}-shell"),
    (f"git reset -q --hard origin/ark/{SLUG}", "nothing changed against"),
    ("true", "outside its area: outside/shell.txt"),
])
def test_a_fixed_that_fails_a_check_asks_marco_and_pushes_nothing(feature, monkeypatch, cmd, why):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    monkeypatch.setenv("FAKE_EUKLIDES_RUN", cmd)
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "fixed", "note": "all good"}')
    repo, _ = feature()
    drive(waits_for_marco)
    [q] = [q for q in state()["questions"] if q["job"] == "shell"]
    assert why in q["text"] and "Proposal: :stop, or say what the job should do" in q["text"]
    assert gitops.remote_sha(repo, f"ark/{SLUG}-shell") is None
    j = state()["jobs"]["shell"]
    assert j["attempts"] == 1 and "rewrite_ok" not in j


def test_a_fixed_without_a_worktree_asks_marco(tmp_path, ark_home):
    c = at_euklides(tmp_path, allowed=("retry", "escalate", "fixed", "ask"))  # tmp_path/shell does not exist
    loop.on_euklides(c, "shell", euk_answer(action="fixed", note="repaired"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and "rewrite_ok" not in j
    [q] = [q for q in c.st["questions"] if q["job"] == "shell"]
    assert "it replied fixed, but the job has no worktree" in q["text"]
    assert f"Proposal: {loop.FIXED_PROPOSAL}" in q["text"]


def _g(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _commit_in(wt, rel, text, msg):
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text(text)
    _g(wt, "add", "-A")
    _g(wt, "commit", "-q", "-m", msg)
    return gitops.rev(wt, "HEAD")


def rewritten_page(runner, tmp_path):
    """The page job's case: on origin its branch is real commit, then the base taken in, then that undone;
    Euklides rebuilt it as the base plus the real commit. Returns the context and the old head."""
    repo, base = runner(), f"ark/{SLUG}"
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), base)
    gitops.fetch(repo)
    wt = tmp_path / "wt" / "page"
    gitops.add_worktree(repo, wt, f"ark/{SLUG}-page", f"origin/{base}")
    real = _commit_in(wt, "src/page/page.txt", "page\n", "feat: page")
    other = tmp_path / "wt" / "mover"
    gitops.add_worktree(repo, other, "ark/mover", f"origin/{base}")
    gitops.push_ref(repo, _commit_in(other, "moved.txt", "m\n", "base moves"), base)
    gitops.fetch(repo)
    _g(wt, "merge", "--no-ff", "-q", "-m", "take the base", f"origin/{base}")
    _g(wt, "revert", "--no-edit", "-m", "1", "HEAD")
    old = gitops.rev(wt, "HEAD")
    gitops.push_ref(repo, old, f"ark/{SLUG}-page")
    _g(wt, "reset", "-q", "--hard", f"origin/{base}")
    _g(wt, "cherry-pick", real)
    st = run.new_state({"repo": "demo", "slug": SLUG, "plan": "/nowhere", "sha256": "0" * 64, "approved_at": "t"})
    mj = job("page", ["T1"], ["src/page/**"])
    st["jobs"]["page"] = {**run.new_job(mj), "status": "euklides", "attempts": 1,
                          "euklides": {"trigger": "blocked", "situation": "non-fast-forward", "kind": "step",
                                       "back": "needs_push", "allowed": ["fixed", "ask"]}}
    c = SimpleNamespace(st=st, dir=tmp_path, base=base, mjobs={"page": mj}, repo=repo, wt=lambda j: wt,
                        branch=lambda j: f"ark/{SLUG}-{j}")
    return c, old


def test_a_rewritten_branch_is_pushed_with_a_lease_after_a_backup(runner, tmp_path):
    c, old = rewritten_page(runner, tmp_path)
    loop.on_euklides(c, "page", euk_answer(action="fixed", note="rebuilt as the base plus the real commit"))
    j = c.st["jobs"]["page"]
    new = gitops.rev(c.wt("page"), "HEAD")
    assert j["status"] == "needs_push" and j["head"] == new and j["rewrite_ok"] is True
    loop.push_job(c, "page")
    backup = f"ark/backup/{SLUG}-page-{old[:8]}"
    assert gitops.remote_sha(c.repo, backup) == old
    assert gitops.remote_sha(c.repo, f"ark/{SLUG}-page") == new
    assert j["status"] == "needs_gate" and "rewrite_ok" not in j
    assert any(backup in e for e in c.st["events"])


def test_a_workers_non_fast_forward_still_blocks(runner, tmp_path):
    c, old = rewritten_page(runner, tmp_path)
    j = c.st["jobs"]["page"]
    j.update(status="needs_push", head=gitops.rev(c.wt("page"), "HEAD"))
    j.pop("euklides")
    loop.push_job(c, "page")
    assert j["status"] == "needs_push" and j["push_tries"] == 1
    assert gitops.remote_sha(c.repo, f"ark/{SLUG}-page") == old
    assert gitops.remote_sha(c.repo, f"ark/backup/{SLUG}-page-{old[:8]}") is None


# ---- raise: Euklides adds budget to a job, up to its margin --------------------------------------

def margin(ark_home, value):
    f = ark_home / "workspace.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "euklides": {"budget_margin": value}}))


def over_budget(tmp_path, **over):
    c = at_euklides(tmp_path, allowed=("raise", "fixed", "ask"), session="s1", usd=1.2, **over)
    c.mjobs["shell"]["budget"]["usd"] = 1
    return c


def test_a_raise_within_the_margin_raises_the_job_and_it_goes_on(tmp_path, ark_home):
    margin(ark_home, 0.5)
    c = over_budget(tmp_path)
    loop.on_euklides(c, "shell", euk_answer(action="raise", usd=0.4, note="one more attempt fixes the import"))
    j = c.st["jobs"]["shell"]
    assert j["usd_raised"] == 0.4 and j["status"] == "needs_worker" and j["euklides_count"] == 1
    assert "one more attempt fixes the import" in j["resume_msg"]
    assert any("raised" in e and "$0.40" in e for e in c.st["events"])
    [row] = euklides_mod.decisions()
    assert row["action"] == "raise" and row["raise_usd"] == 0.4 and row["usd"] == 0.4
    assert not c.st.get("questions")


def test_a_raise_past_the_margin_left_asks_marco_with_the_raise_as_proposal(tmp_path, ark_home):
    margin(ark_home, 0.5)
    c = over_budget(tmp_path)
    loop.on_euklides(c, "shell", euk_answer(action="raise", usd=0.6, note="it needs more"))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and j.get("usd_raised", 0) == 0
    [q] = c.st["questions"]
    assert "raise" in q["text"] and "$0.60" in q["text"] and "$0.50" in q["text"]
    assert j["euklides_ask"]["raise_usd"] == 0.6


def test_the_margin_is_a_total_across_decisions(tmp_path, ark_home):
    margin(ark_home, 0.5)
    c = over_budget(tmp_path)
    loop.on_euklides(c, "shell", euk_answer(action="raise", usd=0.25, note="a little more"))
    j = c.st["jobs"]["shell"]
    assert j["usd_raised"] == 0.25
    j.update(status="euklides", usd=1.3,
             euklides={"trigger": "failure", "situation": "red", "kind": None, "allowed": ["raise", "fixed", "ask"]})
    loop.on_euklides(c, "shell", euk_answer(action="raise", usd=0.3, note="a little more again"))
    assert j["usd_raised"] == 0.25 and j["status"] == "waiting_answer"
    assert j["euklides_ask"]["raise_usd"] == 0.3


def test_marcos_ok_to_a_raise_adds_to_the_job_but_not_to_the_margin(tmp_path, ark_home):
    margin(ark_home, 0.5)
    c = over_budget(tmp_path)
    loop.on_euklides(c, "shell", euk_answer(action="raise", usd=0.6, note="it needs more"))
    loop._apply_answer(c.st, {"id": "q1", "text": "ok"})
    j = c.st["jobs"]["shell"]
    assert j["usd_raised"] == 0.6 and j["marco_raised"] == 0.6 and j["status"] == "needs_worker"
    assert j["note"].endswith("Note: it needs more")
    assert euklides_mod.margin_left(j, 1, 0.5) == 0.5


def test_start_euklides_opens_raise_with_the_margin_and_states_it(tmp_path, ark_home, monkeypatch):
    margin(ark_home, 0.5)
    c = at_euklides(tmp_path, usd=1.2)
    c.mjobs["shell"]["budget"]["usd"] = 1
    j = c.st["jobs"]["shell"]
    j["status"] = "needs_euklides"
    seen = {}
    monkeypatch.setattr(loop.brief, "euklides_prompt", lambda *a, **kw: seen.update(kw) or "prompt")
    monkeypatch.setattr(loop.proc, "start", lambda *a, **kw: None)
    monkeypatch.setattr(loop, "_branch_lists", lambda wt, base: ([], []))
    monkeypatch.setattr(loop, "_dependents", lambda c, job: [])
    monkeypatch.setattr(loop.gitops, "origin_url", lambda repo: "file:///x")
    c.read_report = lambda job: None
    c.plan = None
    loop.start_euklides(c, "shell")
    assert "raise" in j["euklides"]["allowed"] and seen["margin_left"] == 0.5
