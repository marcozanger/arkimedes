import json

import pytest

from ark import cli, forge, gitops, paths
from test_sweep_plan import make_run, wt_of


@pytest.fixture
def repo(runner):
    return runner("demo")


def branches(repo):
    return set(gitops.git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/ark/").split())


def two_worktrees(repo):
    make_run(repo, "notes-edit", jobs={"a": "blocked", "b": "blocked"})
    (wt_of("demo", "notes-edit", "b") / "wip.txt").write_text("wip\n")
    return forge.get().create(repo, base="ark/notes-edit", head="ark/notes-edit-a", title="t", body="b")


def test_dry_run_prints_blocks_and_changes_nothing(repo, capsys):
    pr = two_worktrees(repo)
    before = branches(repo)
    assert cli.main(["sweep", "--dry-run"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "demo/notes-edit · aborted" in out
    a = wt_of("demo", "notes-edit", "a")
    assert any(l.startswith("  worktree  ") and l.endswith(")") and f"demo/notes-edit/a" in l for l in out)
    assert "  branch    ark/notes-edit-a  (local, origin)" in out
    assert f"  pr        {pr['url']}  (close)" in out
    assert any(l.startswith("  held      ") and "demo/notes-edit/b: uncommitted changes" in l for l in out)
    assert out[-1].startswith("would free ") and out[-1].endswith("· 1 worktrees · 2 branches · 1 PRs · 3 held")
    assert a.exists() and branches(repo) == before
    assert gitops.remote_sha(repo, "ark/notes-edit-a")
    assert forge.get().list(repo)[0]["state"] == "OPEN"


def test_sweep_removes_and_reports(repo, capsys):
    pr = two_worktrees(repo)
    assert cli.main(["sweep"]) == 0
    out = capsys.readouterr().out
    assert "removed" in out and "error" not in out
    assert not wt_of("demo", "notes-edit", "a").exists()
    assert wt_of("demo", "notes-edit", "b").exists()
    assert "ark/notes-edit-a" not in branches(repo)
    assert gitops.remote_sha(repo, "ark/notes-edit-a") is None
    assert forge.get().list(repo)[pr["number"] - 1]["state"] == "CLOSED"
    last = out.strip().splitlines()[-1]
    assert last.startswith("freed ") and "1 worktrees" in last
    rows = (paths.ark_home() / "history" / "sweeps.jsonl").read_text().splitlines()
    assert json.loads(rows[0])["repo"] == "demo"


def test_sweep_exits_1_when_an_item_fails(repo, capsys, monkeypatch):
    make_run(repo, "gone", jobs={"a": "blocked"})
    monkeypatch.setattr(gitops, "remove_worktree", lambda r, p: (_ for _ in ()).throw(gitops.GitError("stuck")))
    assert cli.main(["sweep"]) == 1
    out = capsys.readouterr().out
    assert "error" in out and "stuck" in out


def test_nothing_to_sweep(repo, capsys):
    assert cli.main(["sweep"]) == 0
    assert capsys.readouterr().out.strip() == "Nothing to sweep."
    assert cli.main(["sweep", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "Nothing to sweep."


def test_refused_inside_a_runner_process(repo, capsys, monkeypatch):
    make_run(repo, "gone", jobs={"a": "blocked"})
    monkeypatch.setenv("ARK_ROLE", "worker")
    assert cli.main(["sweep"]) == 1
    assert "runner process" in capsys.readouterr().err
    assert wt_of("demo", "gone", "a").exists()
    assert cli.main(["sweep", "--dry-run"]) == 0


def test_works_with_the_daily_sweep_off(repo, capsys):
    ws = paths.ark_home() / "workspace.json"
    data = json.loads(ws.read_text()) if ws.exists() else {}
    data["sweep"] = {"enabled": False}
    ws.write_text(json.dumps(data))
    make_run(repo, "gone", jobs={"a": "blocked"})
    assert cli.main(["sweep"]) == 0
    assert not wt_of("demo", "gone", "a").exists()
