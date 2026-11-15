import json

import pytest

from ark import forge, gitops, paths, sweep
from test_sweep_plan import NOW, commit, make_run, of, sh, targets, wt_of


def log_rows():
    f = paths.ark_home() / "history" / "sweeps.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []


@pytest.fixture
def repo(runner):
    return runner("demo")


def local_branches(repo):
    return set(gitops.git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/ark/").split())


def test_apply_clears_a_run_and_logs_each_deletion(repo):
    f = forge.get()
    make_run(repo, "gone", jobs={"a": "blocked", "b": "blocked"}, scratch=["_decisions"])
    (paths.worktrees_dir() / "demo" / "empty").mkdir()
    pr = f.create(repo, base="ark/gone", head="ark/gone-a", title="t", body="b")
    plans, _ = sweep.plan(NOW, f)
    res = sweep.apply(plans, f, NOW)
    assert res["errors"] == [] and res["held"] == []
    assert not (paths.worktrees_dir() / "demo").exists()
    assert paths.worktrees_dir().is_dir()
    assert local_branches(repo) == set()
    assert all(gitops.remote_sha(repo, b) is None for b in ("ark/gone", "ark/gone-a", "ark/gone-b"))
    closed = f.list(repo)[pr["number"] - 1]
    assert closed["state"] == "CLOSED"
    assert closed["comments"] == ["Closed by the Arkimedes sweep: run demo/gone is aborted."]
    rows = log_rows()
    assert {(r["kind"], r["target"]) for r in rows} == {(i.kind, i.target) for p in plans for i in p.items}
    assert len(rows) == len(res["removed"]) == sum(len(p.items) for p in plans)
    assert all(set(r) == {"at", "repo", "slug", "why", "kind", "target"} for r in rows)
    pr_row = next(r for r in rows if r["kind"] == "pr")
    assert (pr_row["repo"], pr_row["slug"], pr_row["why"], pr_row["at"]) == ("demo", "gone", "aborted", NOW.isoformat())
    assert f"pr:demo/gone:{pr['url']}" in res["removed"]
    assert sweep.plan(NOW, f) == ([], [])


def test_the_pr_is_closed_before_its_branch_goes(repo, monkeypatch):
    f = forge.get()
    make_run(repo, "gone", jobs={"a": "blocked"})
    f.create(repo, base="ark/gone", head="ark/gone-a", title="t", body="b")
    order = []
    real_close, real_del = f.close, gitops.delete_remote_branch
    monkeypatch.setattr(f, "close", lambda *a: (order.append("close"), real_close(*a)))
    monkeypatch.setattr(gitops, "delete_remote_branch",
                        lambda r, b: (order.append(f"remote {b}"), real_del(r, b)))
    sweep.apply(sweep.plan(NOW, f)[0], f, NOW)
    assert order.index("close") < order.index("remote ark/gone-a")


def test_held_items_stay(repo):
    f = forge.get()
    make_run(repo, "gone", jobs={"a": "blocked", "b": "blocked"})
    (wt_of("demo", "gone", "a") / "wip.txt").write_text("wip\n")
    plans, _ = sweep.plan(NOW, f)
    res = sweep.apply(plans, f, NOW)
    assert wt_of("demo", "gone", "a").exists() and not wt_of("demo", "gone", "b").exists()
    assert local_branches(repo) == {"ark/gone-a"}
    assert gitops.remote_sha(repo, "ark/gone-a")
    assert (f"worktree:demo/gone:{wt_of('demo', 'gone', 'a')}", "uncommitted changes") in res["held"]
    again, _ = sweep.plan(NOW, f)
    assert [p.items for p in again] == [[]] and len(of(again, "gone").held) == 3


def test_an_error_skips_the_item_and_its_branch(repo, monkeypatch):
    class Failing(forge.LocalForge):
        def close(self, repo, number, comment):
            raise forge.ForgeError("no closing today")

    f = Failing()
    make_run(repo, "gone", jobs={"a": "blocked", "b": "blocked", "c": "blocked"})
    pr = f.create(repo, base="ark/gone", head="ark/gone-a", title="t", body="b")
    real_remove = gitops.remove_worktree

    def remove(r, path):
        if path.name == "b":
            raise gitops.GitError("stuck")
        real_remove(r, path)
    monkeypatch.setattr(gitops, "remove_worktree", remove)
    plans, _ = sweep.plan(NOW, f)
    res = sweep.apply(plans, f, NOW)
    errors = dict(res["errors"])
    assert errors[f"pr:demo/gone:{pr['url']}"] == "no closing today"
    assert errors[f"worktree:demo/gone:{wt_of('demo', 'gone', 'b')}"] == "stuck"
    assert {"ark/gone-a", "ark/gone-b"} <= local_branches(repo)
    assert gitops.remote_sha(repo, "ark/gone-a") and gitops.remote_sha(repo, "ark/gone-b")
    assert "ark/gone-c" not in local_branches(repo) and gitops.remote_sha(repo, "ark/gone-c") is None
    assert not wt_of("demo", "gone", "c").exists() and not wt_of("demo", "gone", "a").exists()
    logged = {(r["kind"], r["target"]) for r in log_rows()}
    assert ("pr", pr["url"]) not in logged and ("worktree", str(wt_of("demo", "gone", "b"))) not in logged
    assert ("local_branch", "ark/gone-a") not in logged and ("local_branch", "ark/gone-c") in logged


def test_orphans_and_folders_go_bottom_up(repo):
    master = gitops.rev(repo, "origin/master")
    sh("worktree", "add", "-q", "--detach", str(wt_of("demo", "ghost", "job")), master, cwd=repo)
    (paths.worktrees_dir() / "other" / "deep").mkdir(parents=True)
    (paths.worktrees_dir() / "_ticks").mkdir()
    f = forge.get()
    plans, _ = sweep.plan(NOW, f)
    res = sweep.apply(plans, f, NOW)
    assert res["errors"] == []
    assert sorted(p.name for p in paths.worktrees_dir().iterdir()) == ["_ticks"]
    assert gitops.worktrees(repo) == [(gitops.worktrees(repo)[0][0], "master")]
    assert sweep.plan(NOW, f) == ([], [])
