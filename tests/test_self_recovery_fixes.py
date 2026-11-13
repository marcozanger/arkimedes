import json

import pytest
from conftest import git
from test_loop import SLUG, at_euklides, drive, euk_answer, jobs_settled, state, stopping  # noqa: F401
from helpers import job, manifest

from ark import brief, euklides, gitops, loop, run


def test_widen_and_fix_base_open_only_after_their_blocks():
    j = {"usd": 0, "tier": "T1"}
    assert "widen" in euklides.allowed("blocked", j, 2, kind="area")
    assert "widen" not in euklides.allowed("blocked", j, 2, kind="integrate")
    assert "fix_base" in euklides.allowed("blocked", j, 2, kind="integrate", base_red=True)
    assert "fix_base" not in euklides.allowed("blocked", j, 2, kind="integrate", base_red=False)
    assert "fix_base" not in euklides.allowed("blocked", j, 2, kind="integrate", base_red=True,
                                              fixes_used=euklides.MAX_FIX_BASE)


@pytest.mark.parametrize("files", [[], ["a", "b", "c", "d"], ["../x"], ["/abs"], ["docs/*.md"], [3]])
def test_widen_files_must_be_one_to_three_plain_relative_paths(files):
    d = {"action": "widen", "note": "n", "undo": "job", "why": "w", "proposal": None, "files": files}
    assert euklides.parse(d, ["widen", "ask"]) is None


def test_a_widen_keeps_its_files_and_other_actions_drop_them():
    d = {"action": "widen", "note": "n", "undo": "job", "why": "w", "proposal": None, "files": [" docs/a.md "]}
    assert euklides.parse(d, ["widen", "ask"])["files"] == ["docs/a.md"]
    d = {"action": "unblock", "note": "n", "files": ["docs/a.md"]}
    assert "files" not in euklides.parse(d, ["unblock", "ask"])


def test_the_area_is_the_plan_plus_the_runs_grants():
    st = {"grants": {"shell": ["docs/a.md"]}}
    assert run.area(st, "shell", {"area": ["src/shell/**"]}) == ["src/shell/**", "docs/a.md"]
    assert run.area({}, "shell", {"area": ["src/shell/**"]}) == ["src/shell/**"]


def test_the_job_block_shows_the_grants():
    plan = type("P", (), {"sections": {}, "manifest": {}})()
    mj = job("shell", [], ["src/shell/**"])
    monkey = brief.task_texts
    try:
        brief.task_texts = lambda p: {}
        text = "\n".join(brief._job_block(mj, plan, grants=["docs/a.md"]))
        assert "Granted in this run: docs/a.md" in text
        assert "Granted" not in "\n".join(brief._job_block(mj, plan))
    finally:
        brief.task_texts = monkey


def test_changed_lines_counts_added_and_removed(tmp_path):
    git("init", "-q", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "a", cwd=tmp_path)
    (tmp_path / "a.txt").write_text("one\nthree\nfour\n")
    (tmp_path / "b.bin").write_bytes(b"\0\1\2")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "b", cwd=tmp_path)
    assert gitops.changed_lines(tmp_path, "HEAD~1", "HEAD", ["a.txt"]) == 3
    assert gitops.changed_lines(tmp_path, "HEAD~1", "HEAD", ["a.txt", "b.bin"]) is None


def test_a_control_center_replay_fixes_the_base_without_asking(feature, monkeypatch):
    m = manifest(integration_gate="test ! -e bad.txt")
    repo, _ = feature(m)
    loop.cycle()
    git("checkout", "-q", f"ark/{SLUG}", cwd=repo)
    (repo / "bad.txt").write_text("broken\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "an earlier merge left a bad word", cwd=repo)
    git("push", "-q", "origin", f"ark/{SLUG}", cwd=repo)
    git("checkout", "-q", "master", cwd=repo)
    monkeypatch.setenv("FAKE_EUKLIDES", json.dumps({"action": "fix_base", "note": "delete bad.txt",
                                                    "undo": "job", "why": "red on the base", "proposal": None}))
    monkeypatch.setenv("FAKE_FIXBASE", "bad.txt")
    drive(jobs_settled)
    st = state()
    assert {j["status"] for j in st["jobs"].values()} == {"merged"}
    assert not st.get("answers") and not st.get("questions")
    log = gitops.git(repo, "log", "--format=%s", f"origin/ark/{SLUG}")
    assert "fix(base): delete bad.txt (Euklides fix 1)" in log
    assert any("base fix 1 landed" in e for e in st["events"])
    assert st["fixes"][0]["status"] == "landed"


def test_widen_grants_the_files_and_a_later_area_check_passes(feature, monkeypatch):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    monkeypatch.setenv("FAKE_EUKLIDES", json.dumps({"action": "widen", "note": "two docs", "undo": "job",
                                                    "why": "small", "proposal": None,
                                                    "files": ["outside/shell.txt"]}))
    feature()
    drive(jobs_settled)
    st = state()
    assert st["grants"]["shell"] == ["outside/shell.txt"]
    assert st["jobs"]["shell"]["status"] == "merged" and st["jobs"]["shell"]["euklides_count"] == 1
    assert any("widened" in e for e in st["events"]) and not st.get("answers")


def test_widen_over_the_small_fix_rule_asks_marco(feature, monkeypatch):
    monkeypatch.setattr(loop, "SMALL_LINES", 0)
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    monkeypatch.setenv("FAKE_EUKLIDES", json.dumps({"action": "widen", "note": "two docs", "undo": "job",
                                                    "why": "small", "proposal": None,
                                                    "files": ["outside/shell.txt"]}))
    feature()
    drive(lambda: state()["jobs"]["shell"]["status"] == "waiting_answer")
    [q] = state()["questions"]
    assert "small-fix rule" in q["text"] and "outside/shell.txt" in q["text"]
    assert "shell" not in (state().get("grants") or {})


def test_ok_to_a_widen_over_the_rule_grants_it(tmp_path, ark_home, monkeypatch):
    c = at_euklides(tmp_path, trigger="blocked", allowed=("widen", "ask"), kind="area")
    monkeypatch.setattr(loop, "_outside", lambda c, job: ["docs/a.md", "docs/b.md"])
    monkeypatch.setattr(gitops, "rev", lambda wt, ref: "f" * 40)
    loop.on_euklides(c, "shell", euk_answer(action="widen", note="n", undo="job", why="w", proposal=None,
                                            files=["docs/a.md"]))
    [q] = c.st["questions"]
    loop._answered_worker(c.st, q, "ok")
    assert c.st["grants"]["shell"] == ["docs/a.md", "docs/b.md"]
    assert c.st["jobs"]["shell"]["status"] == "needs_push"


def test_widen_that_names_fewer_files_than_changed_asks_marco(tmp_path, ark_home, monkeypatch):
    c = at_euklides(tmp_path, trigger="blocked", allowed=("widen", "ask"), kind="area")
    monkeypatch.setattr(loop, "_outside", lambda c, job: ["docs/a.md", "docs/b.md"])
    loop.on_euklides(c, "shell", euk_answer(action="widen", note="n", undo="job", why="w", proposal=None,
                                            files=["docs/a.md"]))
    assert c.st["jobs"]["shell"]["status"] == "waiting_answer"


def test_a_widen_into_another_jobs_area_asks_marco(tmp_path, ark_home, monkeypatch):
    c = at_euklides(tmp_path, trigger="blocked", allowed=("widen", "ask"), kind="area")
    c.mjobs["ui"] = job("ui", [], ["src/ui/**"])
    monkeypatch.setattr(loop, "_outside", lambda c, job: ["src/ui/x.ts"])
    monkeypatch.setattr(gitops, "changed_lines", lambda *a: 2)
    loop.on_euklides(c, "shell", euk_answer(action="widen", note="n", undo="job", why="w", proposal=None,
                                            files=["src/ui/x.ts"]))
    assert c.st["jobs"]["shell"]["status"] == "waiting_answer"
    assert "ui's area" in c.st["questions"][0]["text"]


def test_a_failed_base_fix_asks_marco_for_every_waiting_job(feature, monkeypatch):
    m = manifest(integration_gate="test ! -e bad.txt")
    repo, _ = feature(m)
    loop.cycle()
    git("checkout", "-q", f"ark/{SLUG}", cwd=repo)
    (repo / "bad.txt").write_text("x\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "bad", cwd=repo)
    git("push", "-q", "origin", f"ark/{SLUG}", cwd=repo)
    git("checkout", "-q", "master", cwd=repo)
    monkeypatch.setenv("FAKE_EUKLIDES", json.dumps({"action": "fix_base", "note": "delete bad.txt",
                                                    "undo": "job", "why": "red", "proposal": None}))
    monkeypatch.delenv("FAKE_FIXBASE", raising=False)  # the fix worker changes nothing: its fix fails
    drive(lambda: state()["jobs"]["shell"]["status"] == "waiting_answer")
    st = state()
    assert st["fixes"][0]["status"] == "failed"
    assert any("base fix 1" in q["text"] for q in st["questions"])


def test_a_lost_fix_process_goes_back_a_step_then_fails(tmp_path, ark_home, monkeypatch):
    st = run.new_state({"repo": "demo", "slug": SLUG, "plan": "/nowhere", "sha256": "0" * 64, "approved_at": "t"})
    st["status"] = "running"
    st["jobs"]["shell"] = {**run.new_job(job("shell", [], ["src/**"])), "status": "waiting_fix", "fix": 1}
    st["fixes"] = [{"n": 1, "job": "shell", "note": "n", "situation": "s", "decision": None, "status": "working",
                    "waiting": {"shell": {"trigger": "blocked", "situation": "s", "kind": "integrate",
                                          "decision": None, "proposal": "p"}}, "lost": 0}]
    monkeypatch.setattr("ark.paths.run_dir", lambda repo, slug: tmp_path)
    loop.reconcile(st)
    assert st["fixes"][0]["status"] == "needs_worker" and st["fixes"][0]["lost"] == 1
    st["fixes"][0]["status"] = "gating"
    loop.reconcile(st)
    assert st["fixes"][0]["status"] == "failed"
    assert st["jobs"]["shell"]["status"] == "waiting_answer"


def test_ps_shows_a_job_waiting_for_a_base_fix(ark_home):
    from ark import ctl
    st = {"repo": "demo", "slug": SLUG}
    row = ctl._ps_row(st, "shell", {"status": "waiting_fix", "fix": 2, "tier": "T1", "attempts": 1, "usd": 0})
    assert "waiting for base fix 2" in " ".join(str(x) for x in row)
