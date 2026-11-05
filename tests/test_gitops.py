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


# ---- hardening (quality review of task 4) ----

def _commit(wt, rel, text, msg="c"):
    p = wt / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", msg], check=True)


def _origin_refs(repo):
    return gitops.git(repo, "ls-remote", "--heads", "origin")


def _ahead_with_upstream(repo):
    """Local master ahead of origin/master and tracking it: a bare `git push` would move it."""
    subprocess.run(["git", "-C", str(repo), "branch", "--set-upstream-to=origin/master"], check=True,
                   capture_output=True)
    _commit(repo, "ahead.txt", "x\n", "ahead")


def test_push_ref_rejects_option_shaped_sha(runner, tmp_path):
    repo = runner()
    before = _origin_refs(repo)
    _ahead_with_upstream(repo)
    marker = tmp_path / "marker"
    for bad in ("--repo=origin", f"--receive-pack=touch {marker}; true ", "", "HEAD", "abc"):
        with pytest.raises(gitops.GitError):
            gitops.push_ref(repo, bad, "ark/x")
    assert _origin_refs(repo) == before
    assert not marker.exists()


def test_push_ref_never_forces_or_deletes(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, base, "ark/x")
    wt = tmp_path / "other"
    gitops.add_worktree(repo, wt, "ark/other", base)
    _commit(wt, "o.txt", "o\n")
    _commit(repo, "r.txt", "r\n")
    first = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, first, "ark/x")
    other = gitops.rev(wt, "HEAD")
    with pytest.raises(gitops.GitError):
        gitops.push_ref(repo, "+" + other, "ark/x")
    with pytest.raises(gitops.GitError):
        gitops.push_ref(repo, other, "ark/x")
    assert gitops.remote_sha(repo, "ark/x") == first


BAD_BRANCHES = ["master", "main", "ARK/x", "+ark/x", "-ark/x", "ark/x:master", "ark/x:refs/heads/master",
                "ark/../master", "ark/x/../../master", "ark//master", "ark/", "ark/x/", "ark/x\n",
                "ark/x master", "ark/*", "ark/x..y", "ark/ x"]


@pytest.mark.parametrize("branch", BAD_BRANCHES)
def test_branch_whitelist_everywhere(runner, tmp_path, branch):
    repo = runner()
    sha = gitops.rev(repo, "HEAD")
    before = _origin_refs(repo)
    with pytest.raises(gitops.GitError):
        gitops.push_ref(repo, sha, branch)
    with pytest.raises(gitops.GitError):
        gitops.push_branch(repo, branch)
    with pytest.raises(gitops.GitError):
        gitops.commit_file(repo, branch, "a.md", "a\n", "m", tmp_path / "wt" / "s")
    assert _origin_refs(repo) == before


def test_changed_files_sees_rename_source_and_unquoted_paths(runner):
    repo = runner()
    _commit(repo, "protected/keep.txt", "k\n" * 20, "add")
    base = gitops.rev(repo, "HEAD")
    subprocess.run(["git", "-C", str(repo), "mv", "protected/keep.txt", "src_keep.txt"], check=True)
    _commit(repo, "src/año.txt", "n\n")
    _commit(repo, 'src/we"ird.txt', "w\n")
    head = gitops.rev(repo, "HEAD")
    assert gitops.changed_files(repo, base, head) == sorted(
        ["protected/keep.txt", "src_keep.txt", "src/año.txt", 'src/we"ird.txt'])


def test_changed_files_refuses_option_shaped_ref(runner, tmp_path):
    repo = runner()
    out = tmp_path / "leak"
    with pytest.raises(gitops.GitError):
        gitops.changed_files(repo, f"--output={out}", "HEAD")
    assert not list(tmp_path.glob("leak*"))


def test_squash_missing_ref_is_an_error_not_a_conflict(runner, tmp_path):
    repo = runner()
    scratch = tmp_path / "wt" / "scratch"
    with pytest.raises(gitops.GitError):
        gitops.squash(repo, "origin/master", "origin/ark/nope", "x", scratch)
    assert not scratch.exists()


def test_squash_works_under_merge_ff_false(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    wt = tmp_path / "w"
    gitops.add_worktree(repo, wt, "ark/a", base)
    _commit(wt, "a.txt", "a\n")
    subprocess.run(["git", "-C", str(repo), "config", "merge.ff", "false"], check=True)
    sha = gitops.squash(repo, "master", "ark/a", "sq", tmp_path / "wt" / "scratch")
    assert sha and gitops.changed_files(repo, base, sha) == ["a.txt"]


def test_squash_conflict_cleans_scratch(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    for branch, text in (("ark/a", "one\n"), ("ark/b", "two\n")):
        wt = tmp_path / branch.replace("/", "-")
        gitops.add_worktree(repo, wt, branch, base)
        _commit(wt, "README.md", text)
    scratch = tmp_path / "wt" / "scratch"
    assert gitops.squash(repo, "ark/a", "ark/b", "x", scratch) is None
    assert not scratch.exists()


def test_add_worktree_resume_and_mismatches(runner, tmp_path):
    repo = runner()
    other = runner("other")
    base = gitops.rev(repo, "HEAD")
    p = tmp_path / "wt" / "job"
    gitops.add_worktree(repo, p, "ark/a", base)
    gitops.add_worktree(repo, p, "ark/a", base)  # resume: fine
    with pytest.raises(gitops.GitError):  # wrong branch
        gitops.add_worktree(repo, p, "ark/b", base)
    subprocess.run(["git", "-C", str(p), "checkout", "-q", "--detach"], check=True)
    with pytest.raises(gitops.GitError):  # worker moved HEAD off the branch
        gitops.add_worktree(repo, p, "ark/a", base)
    plain = tmp_path / "wt" / "plain"
    plain.mkdir()
    with pytest.raises(gitops.GitError):  # not a worktree
        gitops.add_worktree(repo, plain, "ark/a", base)
    inside = other / "sub"
    inside.mkdir()
    with pytest.raises(gitops.GitError):  # plain dir inside another repo
        gitops.add_worktree(repo, inside, "ark/a", base)
    foreign = tmp_path / "wt" / "foreign"
    gitops.add_worktree(other, foreign, "ark/a", gitops.rev(other, "HEAD"))
    with pytest.raises(gitops.GitError):  # worktree of another repo
        gitops.add_worktree(repo, foreign, "ark/a", base)


def test_remove_worktree_clears_registered_and_stale(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    p = tmp_path / "wt" / "job"
    gitops.add_worktree(repo, p, "ark/a", base)
    gitops.remove_worktree(repo, p)
    assert not p.exists()
    stale = tmp_path / "wt" / "stale"
    stale.mkdir()
    (stale / "junk").write_text("x")
    gitops.remove_worktree(repo, stale)
    assert not stale.exists()


def test_remove_worktree_raises_outside_worktrees_dir(runner, tmp_path):
    repo = runner()
    stale = tmp_path / "elsewhere"
    stale.mkdir()
    with pytest.raises(gitops.GitError):
        gitops.remove_worktree(repo, stale)
    assert stale.exists()


def test_squash_recovers_from_stale_scratch(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    wt = tmp_path / "w"
    gitops.add_worktree(repo, wt, "ark/a", base)
    _commit(wt, "a.txt", "a\n")
    stale = tmp_path / "wt" / "scratch"
    stale.mkdir(parents=True)
    assert gitops.squash(repo, "master", "ark/a", "sq", stale)
    assert not stale.exists()


def test_cleanup_error_does_not_mask_the_real_one(runner, tmp_path, monkeypatch):
    repo = runner()
    calls = []

    def boom(r, p):
        calls.append(1)
        if len(calls) > 1:
            raise gitops.GitError("cleanup failed")

    monkeypatch.setattr(gitops, "remove_worktree", boom)
    with pytest.raises(gitops.GitError, match="origin/ark/nope|nope"):
        gitops.squash(repo, "master", "origin/ark/nope", "x", tmp_path / "wt" / "s")
    assert len(calls) == 2


def test_commit_file_twice_is_a_noop(runner, tmp_path):
    repo = runner()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), "ark/x")
    s1 = gitops.commit_file(repo, "ark/x", "docs/x.md", "# D\n", "docs", tmp_path / "wt" / "s")
    s2 = gitops.commit_file(repo, "ark/x", "docs/x.md", "# D\n", "docs", tmp_path / "wt" / "s")
    assert s1 == s2 == gitops.remote_sha(repo, "ark/x")


@pytest.mark.parametrize("rel", ["../escape.txt", "ABS", "a/../../escape.txt"])
def test_commit_file_rejects_paths_outside_scratch(runner, tmp_path, rel):
    repo = runner()
    if rel == "ABS":
        rel = str(tmp_path / "abs-escape.txt")
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), "ark/x")
    before = gitops.remote_sha(repo, "ark/x")
    with pytest.raises(gitops.GitError):
        gitops.commit_file(repo, "ark/x", rel, "x\n", "m", tmp_path / "wt" / "s")
    assert not (tmp_path / "wt" / "escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "abs-escape.txt").exists()
    assert gitops.remote_sha(repo, "ark/x") == before


def test_remote_sha_ignores_suffix_matches(runner, tmp_path):
    repo = runner()
    sha = gitops.rev(repo, "HEAD")
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", f"{sha}:refs/heads/a/refs/heads/ark/x"],
                   check=True)
    assert gitops.remote_sha(repo, "ark/x") is None
    gitops.push_ref(repo, sha, "ark/x")
    assert gitops.remote_sha(repo, "ark/x") == sha


def test_git_times_out_and_does_not_prompt(runner):
    repo = runner()
    import time
    t = time.time()
    with pytest.raises(gitops.GitError, match="timed out"):
        gitops.git(repo, "-c", "core.sshCommand=sleep 5 #", "ls-remote", "ssh://localhost/x", timeout=1)
    assert time.time() - t < 4
    assert gitops.GIT_TIMEOUT == 300
