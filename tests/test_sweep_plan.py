import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from ark import forge, gitops, paths, sweep

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def sh(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def commit(wt, name):
    (wt / name).write_text(name + "\n")
    sh("add", "-A", cwd=wt)
    sh("commit", "-q", "-m", name, cwd=wt)
    return gitops.rev(wt, "HEAD")


def wt_of(repo_name, slug, name):
    return paths.worktrees_dir() / repo_name / slug / name


def write_state(st):
    f = paths.runs_dir() / st["repo"] / st["slug"] / "state.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st))


def make_run(repo, slug, status="aborted", closing="done", jobs=None, days=1, fixes=(), scratch=(),
             base=True, final_pr=None, repo_name="demo", push=True):
    """A run on disk: a worktree with one pushed commit per job on ark/<slug>-<job>, ark/<slug> on origin,
    detached scratch worktrees, and its state (closed `days` ago)."""
    jobs = jobs if jobs is not None else {"a": "merged"}
    master = gitops.rev(repo, "origin/master")
    if base:
        gitops.push_ref(repo, master, f"ark/{slug}")
        sh("branch", "-f", f"ark/{slug}", master, cwd=repo)
    st = {"repo": repo_name, "slug": slug, "plan": "p", "plan_sha": "0" * 64,
          "approved_at": "2026-11-01T00:00:00+00:00", "status": status, "reason": "", "default_branch": "master",
          "base_sha": master, "started": "2026-11-01T00:00:00+00:00", "updated": "2026-11-01T00:00:00+00:00",
          "closing": closing, "final_pr": final_pr, "events": [], "alerts": [], "times_kept": True,
          "closed_at": (NOW - timedelta(days=days)).isoformat(), "jobs": {}, "fixes": []}
    for job, jstatus in jobs.items():
        wt = wt_of(repo_name, slug, job)
        gitops.add_worktree(repo, wt, f"ark/{slug}-{job}", master)
        head = commit(wt, f"{slug}--{job}.txt")
        if push:
            gitops.push_branch(wt, f"ark/{slug}-{job}")
        st["jobs"][job] = {"status": jstatus, "head": head, "pr": None}
    for n in fixes:
        wt = wt_of(repo_name, slug, f"_fix-{n}")
        gitops.add_worktree(repo, wt, f"ark/{slug}-_fix-{n}", master)
        gitops.push_branch(wt, f"ark/{slug}-_fix-{n}")
        st["fixes"].append({"n": n, "status": "landed"})
    for name in scratch:
        sh("worktree", "add", "-q", "--detach", str(wt_of(repo_name, slug, name)), master, cwd=repo)
    write_state(st)
    return st


def of(plans, slug, repo="demo"):
    return next((p for p in plans if (p.repo, p.slug) == (repo, slug)), None)


def targets(plans, kind, slug=None, repo="demo"):
    return {i.target for p in plans for i in p.items if i.kind == kind and (slug is None or (p.repo, p.slug) == (repo, slug))}


def held(plans, slug, repo="demo"):
    p = of(plans, slug, repo)
    return {(i.kind, i.target): why for i, why in (p.held if p else [])}


@pytest.fixture
def repo(runner):
    return runner("demo")


def test_each_row_of_the_table(repo):
    make_run(repo, "gone", "aborted")
    make_run(repo, "shipped", "done", jobs={"a": "merged", "b": "merged"})
    make_run(repo, "stale", "blocked", jobs={"a": "blocked"}, days=15)
    make_run(repo, "busy", "running", closing=None)
    make_run(repo, "fresh", "blocked", jobs={"a": "blocked"}, days=3)
    plans, problems = sweep.plan(NOW, forge.get())
    assert problems == []
    assert of(plans, "gone").why == "aborted"
    assert of(plans, "shipped").why == "done"
    assert of(plans, "stale").why == "abandoned since " + (NOW - timedelta(days=1)).date().isoformat()
    assert targets(plans, "worktree", "gone") == {str(wt_of("demo", "gone", "a"))}
    assert targets(plans, "worktree", "shipped") == {str(wt_of("demo", "shipped", j)) for j in "ab"}
    assert targets(plans, "local_branch", "shipped") == {"ark/shipped-a", "ark/shipped-b", "ark/shipped"}
    assert targets(plans, "remote_branch", "shipped") == {"ark/shipped-a", "ark/shipped-b", "ark/shipped"}
    assert targets(plans, "local_branch", "stale") == {"ark/stale-a", "ark/stale"}
    assert of(plans, "busy") is None and of(plans, "fresh") is None
    every = {i.target for p in plans for i in p.items}
    assert not any("busy" in t or "fresh" in t for t in every)


def test_scratch_worktrees_and_fix_branches(repo):
    make_run(repo, "gone", fixes=[1], scratch=["_integrate-a", "_integrate-a-base", "_decisions"])
    plans, _ = sweep.plan(NOW, forge.get())
    assert targets(plans, "worktree", "gone") == {str(wt_of("demo", "gone", n)) for n in
                                                  ("a", "_fix-1", "_integrate-a", "_integrate-a-base", "_decisions")}
    assert targets(plans, "local_branch", "gone") == {"ark/gone-a", "ark/gone-_fix-1", "ark/gone"}
    assert targets(plans, "remote_branch", "gone") == {"ark/gone-a", "ark/gone-_fix-1", "ark/gone"}


def test_base_branch_waits_for_the_final_pr(repo):
    f = forge.get()
    make_run(repo, "open", "done")
    pr = f.create(repo, base="master", head="ark/open", title="t", body="b")
    st = json.loads((paths.runs_dir() / "demo/open/state.json").read_text())
    write_state({**st, "final_pr": pr})
    plans, _ = sweep.plan(NOW, f)
    assert "ark/open" not in targets(plans, "local_branch", "open") | targets(plans, "remote_branch", "open")
    assert "ark/open-a" in targets(plans, "local_branch", "open")
    f.close(repo, pr["number"], "x")
    plans, _ = sweep.plan(NOW, f)
    assert "ark/open" in targets(plans, "local_branch", "open") & targets(plans, "remote_branch", "open")


def test_only_branches_that_exist(repo):
    make_run(repo, "gone", base=False, push=False, jobs={"a": "merged"})
    plans, _ = sweep.plan(NOW, forge.get())
    assert targets(plans, "local_branch", "gone") == {"ark/gone-a"}
    assert targets(plans, "remote_branch", "gone") == set()


def test_a_name_claimed_by_a_live_run_is_never_planned(repo):
    make_run(repo, "control-center", "aborted", jobs={"ui-page": "blocked", "api": "blocked"})
    gitops.remove_worktree(repo, wt_of("demo", "control-center", "ui-page"))   # the live run checks the name out
    st = make_run(repo, "control-center-ui", "running", closing=None, jobs={"page": "working"}, base=False)
    assert "ark/control-center-ui-page" in gitops.git(repo, "branch", "--list", "ark/*")
    plans, _ = sweep.plan(NOW, forge.get())
    every = targets(plans, "local_branch") | targets(plans, "remote_branch")
    assert "ark/control-center-ui-page" not in every
    assert {"ark/control-center-api", "ark/control-center"} <= targets(plans, "local_branch", "control-center")
    assert {"ark/control-center-api", "ark/control-center"} <= targets(plans, "remote_branch", "control-center")
    assert of(plans, st["slug"]) is None


def test_dirty_worktree_holds_it_and_its_branch(repo):
    make_run(repo, "gone", jobs={"a": "blocked", "b": "blocked"})
    (wt_of("demo", "gone", "a") / "wip.txt").write_text("wip\n")
    plans, _ = sweep.plan(NOW, forge.get())
    h = held(plans, "gone")
    assert h[("worktree", str(wt_of("demo", "gone", "a")))] == "uncommitted changes"
    assert h[("local_branch", "ark/gone-a")] == "uncommitted changes"
    assert h[("remote_branch", "ark/gone-a")] == "uncommitted changes"
    assert str(wt_of("demo", "gone", "a")) not in targets(plans, "worktree")
    assert "ark/gone-a" not in targets(plans, "local_branch") | targets(plans, "remote_branch")
    assert str(wt_of("demo", "gone", "b")) in targets(plans, "worktree", "gone")
    assert "ark/gone-b" in targets(plans, "local_branch", "gone")


def test_unpushed_commits_hold_the_branch(repo):
    st = make_run(repo, "gone", jobs={"a": "blocked", "b": "blocked"})
    commit(wt_of("demo", "gone", "a"), "more.txt")
    commit(wt_of("demo", "gone", "a"), "more2.txt")
    plans, _ = sweep.plan(NOW, forge.get())
    assert held(plans, "gone")[("local_branch", "ark/gone-a")] == "2 commits not on origin"
    assert "ark/gone-b" in targets(plans, "local_branch", "gone")
    assert str(wt_of("demo", "gone", "a")) in targets(plans, "worktree", "gone")
    assert st["jobs"]["a"]["head"]


def test_the_jobs_own_head_is_not_held(repo):
    make_run(repo, "gone", jobs={"a": "blocked"}, push=False)
    plans, _ = sweep.plan(NOW, forge.get())
    assert "ark/gone-a" in targets(plans, "local_branch", "gone")


def test_a_merged_job_squashed_away_is_not_held(repo):
    st = make_run(repo, "gone", "done", jobs={"a": "merged"})
    commit(wt_of("demo", "gone", "a"), "late.txt")
    gitops.delete_remote_branch(repo, "ark/gone-a")
    plans, _ = sweep.plan(NOW, forge.get())
    assert held(plans, "gone") == {}
    assert "ark/gone-a" in targets(plans, "local_branch", "gone")
    assert st["status"] == "done"


def test_a_branch_checked_out_elsewhere_is_in_use(repo, tmp_path):
    make_run(repo, "gone", jobs={"a": "blocked", "b": "blocked"})
    gitops.remove_worktree(repo, wt_of("demo", "gone", "a"))
    outside = tmp_path / "elsewhere"
    sh("worktree", "add", "-q", str(outside), "ark/gone-a", cwd=repo)
    plans, _ = sweep.plan(NOW, forge.get())
    reason = held(plans, "gone")[("local_branch", "ark/gone-a")]
    assert reason.startswith("in use at ") and reason.endswith("elsewhere")
    assert "ark/gone-b" in targets(plans, "local_branch", "gone")


def test_open_prs_of_deleted_branches_are_closed(repo):
    f = forge.get()
    make_run(repo, "gone", jobs={"a": "blocked"})
    pr = f.create(repo, base="ark/gone", head="ark/gone-a", title="t", body="b")
    plans, _ = sweep.plan(NOW, f)
    prs = [i for p in plans for i in p.items if i.kind == "pr"]
    assert [(i.target, i.number, i.repo, i.slug) for i in prs] == [(pr["url"], pr["number"], "demo", "gone")]


def test_orphans_and_empty_folders(repo):
    master = gitops.rev(repo, "origin/master")
    ghost = wt_of("demo", "ghost", "job")
    sh("worktree", "add", "-q", "--detach", str(ghost), master, cwd=repo)
    (paths.worktrees_dir() / "demo" / "empty").mkdir(parents=True)
    (paths.worktrees_dir() / "other").mkdir()
    (paths.worktrees_dir() / "_ticks").mkdir()
    (paths.worktrees_dir() / "_forge").mkdir()
    plans, problems = sweep.plan(NOW, forge.get())
    assert problems == []
    p = of(plans, "ghost")
    assert p.why == "orphan"
    assert [(i.kind, i.target, i.repo) for i in p.items if i.kind != "folder"] == [("worktree", str(ghost), "demo")]
    folders = targets(plans, "folder")
    root = paths.worktrees_dir()
    assert {str(root / "demo" / "empty"), str(root / "other"), str(root / "demo" / "ghost"),
            str(root / "demo")} == folders


def test_folders_of_live_runs_stay(repo):
    make_run(repo, "busy", "running", closing=None)
    (paths.worktrees_dir() / "demo" / "busy" / "_empty").mkdir()
    plans, _ = sweep.plan(NOW, forge.get())
    assert plans == []


def test_unreadable_state_is_a_problem_not_an_orphan(repo):
    make_run(repo, "broken")
    (paths.runs_dir() / "demo" / "broken" / "state.json").write_text("{not json")
    plans, problems = sweep.plan(NOW, forge.get())
    assert plans == []
    assert len(problems) == 1 and "broken" in problems[0]
