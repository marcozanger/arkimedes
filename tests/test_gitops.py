import subprocess

import pytest

from ark import gitops


def test_default_branch_and_rev(runner):
    repo = runner()
    assert gitops.default_branch(repo) == "master"
    assert gitops.rev(repo, "origin/master") == gitops.rev(repo, "HEAD")


def test_push_only_to_ark_branches(runner):
    repo = runner()
    sha = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, sha, "ark/x")
    assert gitops.remote_sha(repo, "ark/x") == sha
    assert gitops.remote_sha(repo, "ark/none") is None
    with pytest.raises(gitops.GitError):
        gitops.push_ref(repo, sha, "master")


def test_worktree_changes_and_squash(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, base, "ark/x")
    gitops.fetch(repo)
    wt = tmp_path / "wt" / "job"
    gitops.add_worktree(repo, wt, "ark/x-job", "origin/ark/x")
    (wt / "src").mkdir()
    (wt / "src" / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", "a"], check=True)
    assert gitops.is_clean(wt)
    head = gitops.rev(wt, "HEAD")
    assert gitops.changed_files(repo, base, head) == ["src/a.txt"]
    gitops.push_branch(wt, "ark/x-job")
    gitops.fetch(repo)
    sha = gitops.squash(repo, "origin/ark/x", "origin/ark/x-job", "squash", tmp_path / "wt" / "scratch")
    assert sha and gitops.changed_files(repo, base, sha) == ["src/a.txt"]
    assert not (tmp_path / "wt" / "scratch").exists()


def test_squash_conflict_returns_none(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    for branch, text in (("ark/a", "one\n"), ("ark/b", "two\n")):
        wt = tmp_path / branch.replace("/", "-")
        gitops.add_worktree(repo, wt, branch, base)
        (wt / "README.md").write_text(text)
        subprocess.run(["git", "-C", str(wt), "commit", "-qam", branch], check=True)
        gitops.push_branch(wt, branch)
    gitops.fetch(repo)
    assert gitops.squash(repo, "origin/ark/a", "origin/ark/b", "x", tmp_path / "scratch") is None


def test_commit_file_on_branch(runner, tmp_path):
    repo = runner()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), "ark/x")
    sha = gitops.commit_file(repo, "ark/x", "docs/decisions/x.md", "# D\n", "docs: d", tmp_path / "scratch")
    assert gitops.remote_sha(repo, "ark/x") == sha
