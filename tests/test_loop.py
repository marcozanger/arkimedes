import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import approve, job, manifest, plan_text

from ark import forge, gitops, integrate, loop, merge, paths, proc, run
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


def test_jobs_merge_in_order(feature):
    repo, _ = feature()
    drive(jobs_settled)
    st = state()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"]
    gitops.fetch(repo)
    files = gitops.changed_files(repo, st["base_sha"], f"origin/ark/{SLUG}")
    assert files == ["src/shell/shell.txt", "src/ui/ui.txt"]
    prs = forge.LocalForge().list(repo)
    assert [(p["base"], p["head"], p["state"]) for p in prs] == [
        (f"ark/{SLUG}", f"ark/{SLUG}-shell", "MERGED"), (f"ark/{SLUG}", f"ark/{SLUG}-ui", "MERGED")]
    log = gitops.git(repo, "log", "--format=%s", f"origin/ark/{SLUG}")
    assert log.splitlines()[:2] == ["feat: ui", "feat: shell"]
    assert gitops.git(repo, "rev-parse", "origin/master") == st["base_sha"]     # default branch untouched


def test_outside_area_blocks_the_job_and_its_dependents(feature, monkeypatch):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    repo, _ = feature()
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and "outside its area" in st["jobs"]["shell"]["reason"]
    assert st["jobs"]["ui"]["status"] == "blocked"
    assert gitops.remote_sha(repo, f"ark/{SLUG}-shell") is None                 # never pushed


def test_failed_attempt_is_judged_and_retried(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1")
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 2 and j["tier_path"] == ["T1"]
    assert any("judge: retry" in e for e in state()["events"])


def test_review_fix_resumes_the_worker(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FIX", "shell")
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["review_rounds"] == 1 and j["attempts"] == 2


def test_red_integration_gate_never_merges(feature):
    repo, _ = feature(manifest(integration_gate="exit 1"))
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked"
    assert "integration gate failed twice" in st["jobs"]["shell"]["reason"]
    assert st["jobs"]["shell"]["attempts"] == 2                                 # sent back once
    gitops.fetch(repo)
    assert gitops.rev(repo, f"origin/ark/{SLUG}") == st["base_sha"]


def test_allowed_actions():
    j = {"tier": "T1", "tier_attempts": 1}
    assert loop.allowed_actions(j) == ["block", "retry", "escalate"]
    assert loop.allowed_actions({**j, "tier_attempts": 2}) == ["block", "escalate"]
    assert loop.allowed_actions({"tier": "T2", "tier_attempts": 2}) == ["block"]


def test_existing_integration_branch_keeps_the_run_waiting(feature):
    repo, _ = feature()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), f"ark/{SLUG}")
    loop.cycle()
    st = state()
    assert st["status"] == "waiting" and "already exists" in st["reason"]
    assert not proc.active(paths.run_dir("demo", SLUG))


# ---- beyond the plan's list: each of these is a way an unattended loop could go wrong ------------

def bare(tmp_path, status="working", **over):
    """A context with no repo behind it, for handlers that decide from a receipt alone."""
    st = run.new_state({"repo": "demo", "slug": SLUG, "plan": "/nowhere", "sha256": "0" * 64, "approved_at": "t"})
    mj = job("shell", ["T1"], ["src/shell/**"])
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
    drive(lambda: "holders_since" in state()["jobs"]["shell"])
    for _ in range(3):
        loop.cycle()
    st = state()
    j = st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and j["attempts"] == 1
    assert len([e for e in st["events"] if "ALERT" in e and "4242" in e]) == 1
    holders.clear()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 2 and "holders_since" not in j


@pytest.mark.parametrize("holders", [lambda p: [4242], "error"])
def test_holders_that_persist_block_the_job_and_not_knowing_counts_as_holders(feature, monkeypatch, holders):
    feature()
    def fails(p):
        raise proc.CwdHoldersError("lsof failed")
    monkeypatch.setattr(proc, "cwd_holders", fails if holders == "error" else holders)
    alerts = loop.cycle()[1] + loop.cycle()[1]
    assert state()["jobs"]["shell"]["status"] == "pending"
    assert not proc.active(paths.run_dir("demo", SLUG))
    assert len([a for a in alerts if "shell waits" in a]) == 1
    monkeypatch.setattr(loop, "HOLDERS_PATIENCE", 0)
    time.sleep(0.01)
    loop.cycle()
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and "worktree" in st["jobs"]["shell"]["reason"]


def test_a_held_job_gives_its_writer_slot_to_the_next(feature, monkeypatch, ark_home):
    limits(ark_home, writers_per_repo=1)
    feature(manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]), job("ui", ["T3"], ["src/ui/**"])]))
    monkeypatch.setattr(proc, "cwd_holders", lambda p: [4242] if Path(p).name == "shell" else [])
    loop.cycle()
    st = state()
    assert st["jobs"]["shell"]["status"] == "pending" and st["jobs"]["ui"]["status"] == "working"
    monkeypatch.setattr(proc, "cwd_holders", lambda p: [])
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
    assert c.st["jobs"]["shell"]["status"] == "needs_judge"


# -- receipts

@pytest.mark.parametrize("status,kind", [("gating", "gate"), ("reviewing", "review"), ("integrating", "integrate"),
                                         ("judging", "judge"), ("working", "worker")])
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
    assert c.st["jobs"]["shell"]["status"] == "needs_judge" and "ZeroDivisionError" in c.st["jobs"]["shell"]["failure"]


# -- the worker's receipt

@pytest.mark.parametrize("r", [receipt("STATUS: DONE", exit=1), receipt("STATUS: DONE", is_error=True)])
def test_a_worker_that_ended_in_error_is_a_failure_whatever_it_says(tmp_path, r):
    c = bare(tmp_path)
    loop.on_worker(c, "shell", r)
    assert c.st["jobs"]["shell"]["status"] == "needs_judge" and "error" in c.st["jobs"]["shell"]["failure"]


@pytest.mark.parametrize("r,done", [(receipt("STATUS: FAILED — no"), True), (receipt(reason="setup failed: x", exit=1), False),
                                    (receipt(reason="timeout: over its time limit", exit=-15), False)])
def test_setup_counts_as_done_only_when_the_process_got_through_it(tmp_path, r, done):
    c = bare(tmp_path)
    loop.on_worker(c, "shell", r)
    assert c.st["jobs"]["shell"]["setup_done"] is done


def test_a_file_name_that_is_not_utf8_cannot_break_the_state(tmp_path, monkeypatch):
    c = bare(tmp_path)
    monkeypatch.setattr(gitops, "is_clean", lambda wt: True)
    monkeypatch.setattr(gitops, "changed_files", lambda *a: ["bad\udcff.txt"])
    loop.on_worker(c, "shell", receipt("STATUS: DONE"))
    assert c.st["jobs"]["shell"]["status"] == "blocked" and "outside its area" in c.st["jobs"]["shell"]["reason"]
    run.render(c.st).encode("utf-8")


# -- the judge

JUDGE_GARBAGE = [
    receipt("no json here"), receipt('{"action": "rm -rf /", "note": "x"}'), receipt('{"action": "retry"}'),
    receipt('{"action": "retry", "note": "x", "extra": 1}'), receipt('{"action": ["retry"], "note": "x"}'),
    receipt('{"action": "retry", "note": 5}'), receipt('{"action": "retry", "note": "x"}', exit=1),
    receipt('{"action": "retry", "note": "x"}', is_error=True), receipt('{"action": "retry", "note": "x"}', reason="timeout"),
]


@pytest.mark.parametrize("r", JUDGE_GARBAGE)
def test_an_unusable_judge_answer_never_runs(tmp_path, r):
    c = bare(tmp_path, status="judging", tier="T2", tier_attempts=1, failure="red", tier_path=["T2"])
    loop.on_judge(c, "shell", r)
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_worker" and j["tier"] == "T2" and "no usable answer" in j["note"]


def test_a_retry_the_job_has_no_right_to_becomes_an_escalation(tmp_path):
    c = bare(tmp_path, status="judging", tier="T1", tier_attempts=2, failure="red", session="s")
    loop.on_judge(c, "shell", receipt('{"action": "retry", "note": "again"}'))
    j = c.st["jobs"]["shell"]
    assert j["tier"] == "T2" and j["tier_path"] == ["T1", "T2"] and j["tier_attempts"] == 0 and j["session"] is None


def test_an_escalation_at_the_top_tier_becomes_a_block_when_nothing_else_is_left(tmp_path):
    c = bare(tmp_path, status="judging", tier="T2", tier_attempts=2, failure="red")
    loop.on_judge(c, "shell", receipt('{"action": "escalate", "note": "x"}'))
    assert c.st["jobs"]["shell"]["status"] == "blocked"


def test_a_judge_that_always_retries_cannot_retry_past_the_limit(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1,shell:2")
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["tier_path"] == ["T1", "T2"] and j["attempts"] == 3


def test_every_tier_exhausted_blocks_the_job_and_its_dependents(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1,shell:2,shell:3,shell:4")
    feature()
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and st["jobs"]["shell"]["attempts"] == 4
    assert "no retry or escalation left" in st["jobs"]["shell"]["reason"]
    assert st["jobs"]["ui"]["status"] == "blocked"


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
    assert c.st["jobs"]["shell"]["status"] == "blocked"


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
    loop.on_integrate(c, "shell", r)
    assert j["status"] == "blocked" and "twice without a result" in j["reason"]


SKIPPED = json.dumps({"ok": False, "base": None, "head": "abc", "reason": "the plan changed after its approval", "tail": ""})


def test_a_check_that_could_not_run_is_not_charged_to_the_worker(tmp_path):
    c = bare(tmp_path, status="integrating")
    for _ in range(loop.SKIP_ALERT_AFTER + 5):
        loop.on_integrate(c, "shell", receipt(SKIPPED, exit=1))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_integrate" and j["integrate_fails"] == 0 and j["attempts"] == 0
    assert merge.read_record(tmp_path, "shell", "integrate") is None
    assert len(c.st["alerts"]) == 1


def test_a_check_that_could_not_run_gives_its_heavy_slot_to_the_next_job(tmp_path):
    c = bare(tmp_path, status="integrating", heavy=True)
    loop.on_integrate(c, "shell", receipt(SKIPPED, exit=1))
    j = c.st["jobs"]["shell"]
    assert j["not_before"] > time.time()
    slots = loop.Slots([c.st], {"writers_per_repo": 1, "writers_total": 1, "heavy_gates": 1})
    assert loop._next_step(c, "shell", j, slots) is None and slots.heavy == 0


def test_a_check_that_never_runs_finally_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "INFRA_LIMIT", 3)
    c = bare(tmp_path, status="integrating")
    for _ in range(2):
        loop.on_integrate(c, "shell", receipt(SKIPPED, exit=1))
    assert c.st["jobs"]["shell"]["status"] == "needs_integrate"
    loop.on_integrate(c, "shell", receipt(SKIPPED, exit=1))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "blocked" and "could not run" in j["reason"]


def test_a_check_that_ran_ends_the_waiting(tmp_path):
    c = bare(tmp_path, status="integrating", integrate_skips=3, not_before=time.time() + 60)
    loop.on_integrate(c, "shell", receipt(json.dumps({"ok": True, "base": "b" * 40, "head": "abc", "reason": "", "tail": ""})))
    assert "not_before" not in c.st["jobs"]["shell"] and c.st["jobs"]["shell"]["integrate_skips"] == 0


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
    assert j["status"] == "needs_merge" and j["integrate_lost"] == 0 and j["integrate_skips"] == 0


def test_a_red_gate_full_of_non_ascii_still_reaches_the_loop_as_red(feature):
    gate = f"{sys.executable} -c \"import sys; sys.stdout.buffer.write(b'\\xc3\\xa9' * 3000)\"; exit 1"
    feature(manifest(integration_gate=gate))
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and j["integrate_fails"] == 2 and j["reason"].endswith("(exit 1)")
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

@pytest.mark.parametrize("e", [gitops.GitError("offline"), forge.ForgeError("offline")])
def test_a_git_or_forge_error_keeps_retrying_and_alerts_once(tmp_path, e):
    c = bare(tmp_path, status="needs_merge")
    for _ in range(loop.ERROR_ALERT_AFTER * 2):
        loop._error(c, "shell", e)
    assert c.st["jobs"]["shell"]["status"] == "needs_merge"
    assert len(c.st["alerts"]) == 1 and "offline" in c.st["alerts"][0]


@pytest.mark.parametrize("e", [ValueError("embedded null byte"), OSError("is a directory")])
def test_any_other_error_blocks_after_the_limit_because_it_will_not_heal(tmp_path, e):
    c = bare(tmp_path, status="needs_worker")
    for _ in range(loop.ERROR_ALERT_AFTER - 1):
        loop._error(c, "shell", e)
    assert c.st["jobs"]["shell"]["status"] == "needs_worker" and not c.st["alerts"]
    loop._error(c, "shell", e)
    j = c.st["jobs"]["shell"]
    assert j["status"] == "blocked" and str(e) in j["reason"] and len(c.st["alerts"]) == 1


def test_a_step_that_works_clears_the_error_and_one_job_does_not_stop_the_next(tmp_path, monkeypatch):
    c = bare(tmp_path, status="needs_merge")
    c.st["jobs"]["ui"] = {**run.new_job(job("ui", [], [])), "status": "needs_merge"}
    seen = []
    def merge_it(c, j):
        seen.append(j)
        if j == "shell" and seen.count("shell") == 1:
            raise forge.ForgeError("gh is down")
    monkeypatch.setattr(loop, "do_merge", merge_it)
    slots = loop.Slots([c.st], {"writers_per_repo": 1, "writers_total": 1, "heavy_gates": 1})
    loop.schedule(c, slots)
    assert seen == ["shell", "ui"] and c.st["jobs"]["shell"]["errors"] == 1
    loop.schedule(c, slots)
    assert "errors" not in c.st["jobs"]["shell"] and "error" not in c.st["jobs"]["shell"]


# -- crashes between a process and the saved state

@pytest.mark.parametrize("status,kind,after", [("working", "worker", "needs_worker"), ("gating", "gate", "needs_gate"),
                                               ("reviewing", "review", "needs_review"),
                                               ("integrating", "integrate", "needs_integrate"), ("judging", "judge", "needs_judge")])
def test_a_process_gone_without_a_result_goes_back_a_step(ark_home, status, kind, after, tmp_path):
    st = bare(tmp_path, status=status).st
    loop.reconcile(st)
    assert st["jobs"]["shell"]["status"] == after
    assert loop.RUNNING[status] == kind


@pytest.mark.parametrize("status,kind,after,attempts", [
    ("pending", "worker", "working", 1), ("needs_worker", "worker", "working", 1), ("needs_gate", "gate", "gating", 0),
    ("needs_review", "review", "reviewing", 0), ("needs_integrate", "integrate", "integrating", 0),
    ("needs_judge", "judge", "judging", 0)])
def test_a_process_the_state_missed_is_adopted_never_started_twice(ark_home, status, kind, after, attempts, tmp_path):
    st = bare(tmp_path, status=status).st
    d = paths.run_dir("demo", SLUG)
    d.mkdir(parents=True)
    (d / f"shell.{kind}.proc").write_text("{}")
    loop.reconcile(st)
    assert st["jobs"]["shell"]["status"] == after and st["jobs"]["shell"]["attempts"] == attempts


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
    assert state()["status"] == "running" and state()["jobs"]["shell"]["status"] == "merged"


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
    assert state()["status"] == "running" and state()["jobs"]["shell"]["status"] == "merged"


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
    return receipt("Could not reach the model", **{"exit": 1, "is_error": None, "start": now - 1, "end": now, **over})


@pytest.mark.parametrize("r", [down(), down(is_error=True, usd=0), down(is_error=True), down(exit=-9)])
@pytest.mark.parametrize("status,kind,back", [("working", "worker", "needs_worker"), ("reviewing", "review", "needs_review"),
                                              ("judging", "judge", "needs_judge")])
def test_a_process_that_learned_nothing_charges_nothing(tmp_path, monkeypatch, r, status, kind, back):
    c = bare(tmp_path, status=status, attempts=1, failure="red")
    consume_one(monkeypatch, c, f"shell.{kind}", r)
    j = c.st["jobs"]["shell"]
    assert j["status"] == back and j["tier_attempts"] == 0 and j["review_errors"] == 0 and j["tier_path"] == ["T1"]
    assert j["not_before"] > time.time() and j["infra_tries"] == 1
    assert len(c.st["alerts"]) == 1 and "unreachable" in c.st["alerts"][0]


@pytest.mark.parametrize("r", [down(is_error=True, usd=0.5), down(start=time.time() - 1000), down(start=None),
                               down(reason="timeout: over its time limit"), down(reason="setup failed: x"),
                               down(exit=0)])
def test_a_process_that_did_something_is_charged_as_usual(tmp_path, monkeypatch, r):
    c = bare(tmp_path, status="working")
    consume_one(monkeypatch, c, "shell.worker", r)
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_judge" and j["tier_attempts"] == 1 and "not_before" not in j


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


def test_the_backoff_grows_and_has_a_ceiling(tmp_path, monkeypatch):
    c = bare(tmp_path, status="working")
    j = c.st["jobs"]["shell"]
    waits = []
    for _ in range(9):
        j["status"] = "working"
        consume_one(monkeypatch, c, "shell.worker", down())
        waits.append(round(j["not_before"] - time.time()))
    assert waits == [15, 30, 60, 120, 240, 480, 900, 900, 900]
    assert len(c.st["alerts"]) == 1


def test_a_model_down_for_too_long_finally_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "INFRA_LIMIT", 3)
    c = bare(tmp_path, status="working")
    j = c.st["jobs"]["shell"]
    for _ in range(3):
        assert j["status"] != "blocked"
        j["status"] = "working"
        consume_one(monkeypatch, c, "shell.worker", down())
    assert j["status"] == "blocked" and "unreachable" in j["reason"] and j["tier_attempts"] == 0


def test_a_real_answer_ends_the_waiting(tmp_path, monkeypatch):
    c = bare(tmp_path, status="working", infra_tries=4, not_before=1.0)
    consume_one(monkeypatch, c, "shell.worker", receipt("STATUS: FAILED — no"))
    j = c.st["jobs"]["shell"]
    assert "infra_tries" not in j and "not_before" not in j and j["tier_attempts"] == 1


def test_model_down_blocks_nothing_and_the_run_goes_on_when_it_returns(feature, monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "BACKOFF_FIRST", 0)
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
    assert len([a for a in alerts if "unreachable" in a]) == 1
    monkeypatch.delenv("FAKE_DOWN")
    drive(jobs_settled)
    st = state()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"]
    assert st["jobs"]["shell"]["attempts"] == 1 and st["jobs"]["shell"]["tier"] == "T1"


def test_a_retry_after_the_model_was_down_keeps_the_feedback(feature, monkeypatch):
    monkeypatch.setattr(loop, "BACKOFF_FIRST", 0)
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
    assert len(started) == 3 and "src/x:1" in " ".join(started[2]) and "--resume" in started[2]
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
    assert not failures and j["status"] == "merged" and "errors" not in j
    assert (tmp_path / "fake" / "shell.worker.count").read_text() == "1"
    assert j["tier_attempts"] == 0 and j["attempts"] == 1


def test_a_leftover_job_branch_blocks_at_once_without_spending(feature, tmp_path):
    repo, _ = feature()
    loop.cycle()
    sha = leftover(repo, f"ark/{SLUG}-shell")
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and f"ark/{SLUG}-shell" in j["reason"] and "fast-forward" in j["reason"]
    assert (tmp_path / "fake" / "shell.worker.count").read_text() == "1" and j["tier_path"] == ["T1"]
    assert gitops.remote_sha(repo, f"ark/{SLUG}-shell") == sha                  # left for Marco, not overwritten


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
    assert j["status"] == "needs_push" and j["errors"] == 1 and "network down" in j["error"]


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
def test_a_judge_note_with_bad_text_is_stored_clean(tmp_path, name, dirty, clean):
    c = bare(tmp_path, status="judging", failure="red", session="s")
    loop.on_judge(c, "shell", receipt(json.dumps({"action": "retry", "note": dirty})))
    j = c.st["jobs"]["shell"]
    assert j["note"] == clean and clean in j["resume_msg"] and dirty not in j["resume_msg"]


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
    for _ in range(3):
        loop.cycle()
    st = state()
    assert st["status"] == "held" and st["plan_sha"] == old


# -- the guards, one by one: each test fails when its guard is removed

def test_a_red_job_gate_goes_to_the_judge_and_is_recorded_red(tmp_path):
    c = bare(tmp_path, status="gating")
    loop.on_gate(c, "shell", receipt("assert failed", exit=1))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_judge" and j["tier_attempts"] == 1 and "job gate red" in j["failure"]
    rec = merge.read_record(tmp_path, "shell", "gate")
    assert rec["ok"] is False and rec["exit"] == 1 and "assert failed" in rec["tail"]


def test_a_job_whose_gate_stays_red_gets_no_pr_and_never_merges(feature):
    red = job("shell", ["T1", "T2"], ["src/shell/**"], gate="exit 1")
    repo, _ = feature(manifest(jobs=[red, job("ui", ["T3"], ["src/ui/**"], after=["shell"])]))
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and st["jobs"]["shell"]["pr"] is None
    assert st["jobs"]["ui"]["status"] == "blocked" and forge.LocalForge().list(repo) == []
    assert merge.read_record(paths.run_dir("demo", SLUG), "shell", "gate")["ok"] is False


def test_a_gate_stopped_by_its_time_limit_is_red_even_with_exit_zero(tmp_path):
    c = bare(tmp_path, status="gating")
    loop.on_gate(c, "shell", receipt(exit=0, reason="timeout: over its time limit"))
    assert c.st["jobs"]["shell"]["status"] == "needs_judge"
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
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "blocked" and "outside/shell.txt" in j["reason"] and j["pr"] is None


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
    assert j["status"] == "needs_judge" and "3 review rounds" in j["failure"] and j["tier_attempts"] == 1


def test_a_verdict_that_is_neither_clean_nor_fix_is_no_verdict(tmp_path):
    c = bare(tmp_path, status="reviewing")
    loop.on_review(c, "shell", receipt(json.dumps({"verdict": "MAYBE", "findings": ["x"]})))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_review" and j["review_errors"] == 1 and j["review_rounds"] == 0


def test_an_escalation_starts_its_review_rounds_over(tmp_path):
    c = bare(tmp_path, status="judging", review_rounds=2, session="s", failure="red")
    loop.on_judge(c, "shell", receipt(json.dumps({"action": "escalate", "note": "harder"})))
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


def test_a_refused_merge_blocks_and_never_reaches_the_judge(at_merge, monkeypatch):
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
