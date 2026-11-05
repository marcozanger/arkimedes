import os
import shutil
import signal
import subprocess
import sys
import time

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
    t = time.time()
    with pytest.raises(gitops.GitError, match="timed out"):
        gitops.git(repo, "-c", "core.sshCommand=sleep 5 #", "ls-remote", "ssh://localhost/x", timeout=1)
    assert time.time() - t < 4
    assert gitops.GIT_TIMEOUT == 300


# ---- second round (quality re-review of task 4): whole classes, not single values ----

def _branch_x(runner):
    repo = runner()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), "ark/x")
    return repo


def _objects(repo):
    """Real object ids that are not commits."""
    subprocess.run(["git", "-C", str(repo), "tag", "-a", "-m", "t", "v1"], check=True)
    out = {}
    for name, spec in (("tag", "refs/tags/v1"), ("tree", "HEAD^{tree}"), ("blob", "HEAD:README.md")):
        out[name] = subprocess.run(["git", "-C", str(repo), "rev-parse", spec], check=True, capture_output=True,
                                   text=True).stdout.strip()
    return out


def test_push_ref_only_takes_the_sha_of_a_real_commit(runner):
    repo = _branch_x(runner)
    before = _origin_refs(repo)
    assert "refs/heads/ark/x" in before
    bad = ["0" * 40, "0" * 64, "f" * 40, "f" * 64, "0" * 41, *_objects(repo).values()]
    for sha in bad:
        with pytest.raises(gitops.GitError):
            gitops.push_ref(repo, sha, "ark/x")
    with pytest.raises(gitops.GitError):  # same value, branch that does not exist on origin yet
        gitops.push_ref(repo, "0" * 40, "ark/new")
    assert _origin_refs(repo) == before


def test_push_ref_null_sha_in_a_sha256_repo(tmp_path):
    bare, repo = tmp_path / "o.git", tmp_path / "r"
    subprocess.run(["git", "init", "-q", "--bare", "--object-format=sha256", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", "--object-format=sha256", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
    _commit(repo, "a.txt", "a\n")
    sha = gitops.rev(repo, "HEAD")
    assert len(sha) == 64
    gitops.push_ref(repo, sha, "ark/x")
    for bad in ("0" * 64, "0" * 40):
        with pytest.raises(gitops.GitError):
            gitops.push_ref(repo, bad, "ark/x")
    assert gitops.remote_sha(repo, "ark/x") == sha


# A process that leaves git's group and keeps its stderr open: a daemon started by a hook.
DAEMON = """import os, sys, time
os.setsid()
with open(sys.argv[1], "w") as f:
    f.write(str(os.getpid()))
time.sleep(float(sys.argv[2]))
"""


@pytest.fixture
def daemon_hook(tmp_path):
    """Install a pre-push hook that starts such a daemon, then runs `after`. Kills the daemon at the end."""
    pidfile = tmp_path / "daemon.pid"
    script = tmp_path / "daemon.py"
    script.write_text(DAEMON)

    def install(repo, after=""):
        hook = repo / ".git" / "hooks" / "pre-push"
        hook.write_text(f'#!/bin/sh\n"{sys.executable}" "{script}" "{pidfile}" 30 &\n'
                        f'while [ ! -s "{pidfile}" ]; do sleep 0.05; done\n{after}\n')
        hook.chmod(0o755)

    yield install
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_git_that_finished_is_not_held_by_a_daemon_on_its_pipes(runner, daemon_hook, monkeypatch):
    repo = runner()
    monkeypatch.setattr(gitops, "GIT_TIMEOUT", 10)
    daemon_hook(repo)
    sha = gitops.rev(repo, "HEAD")
    t = time.time()
    gitops.push_ref(repo, sha, "ark/x")  # git's real outcome: a success, right away
    assert time.time() - t < 5
    assert gitops.remote_sha(repo, "ark/x") == sha


def test_timeout_is_bounded_with_a_daemon_on_the_pipes(runner, daemon_hook, tmp_path, monkeypatch):
    repo = runner()
    monkeypatch.setattr(gitops, "GIT_TIMEOUT", 1)
    hook_pid = tmp_path / "hook.pid"
    daemon_hook(repo, f'echo $$ > "{hook_pid}"\nsleep 30')
    t = time.time()
    with pytest.raises(gitops.GitError, match="timed out"):
        gitops.push_ref(repo, gitops.rev(repo, "HEAD"), "ark/x")
    assert time.time() - t < 5
    assert gitops.remote_sha(repo, "ark/x") is None
    deadline = time.time() + 3  # what stayed in git's group is dead
    while time.time() < deadline and _alive(int(hook_pid.read_text())):
        time.sleep(0.05)
    assert not _alive(int(hook_pid.read_text()))


def _alive(pid):
    r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(r.stdout.strip()) and not r.stdout.strip().startswith("Z")


STALL = ("-c", "core.sshCommand=sleep 5 #", "ls-remote", "ssh://localhost/x")


@pytest.mark.parametrize("error", [PermissionError, ProcessLookupError, OSError])
def test_timeout_when_killpg_fails_is_still_a_git_error(runner, monkeypatch, error):
    repo = runner()

    def refuse(pid, sig):
        raise error(1, "refused")

    monkeypatch.setattr(os, "killpg", refuse)
    t = time.time()
    with pytest.raises(gitops.GitError, match="timed out"):
        gitops.git(repo, *STALL, timeout=1)
    assert time.time() - t < 4


def test_timeout_is_bounded_when_git_cannot_be_killed(runner, monkeypatch):
    repo = runner()
    monkeypatch.setattr(gitops, "_KILL_WAIT", 0.2)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: None)
    monkeypatch.setattr(subprocess.Popen, "kill", lambda self: None)
    t = time.time()
    with pytest.raises(gitops.GitError, match="timed out"):
        gitops.git(repo, *STALL, timeout=1)
    assert time.time() - t < 3


def test_git_that_cannot_start_is_a_git_error(runner, monkeypatch):
    repo = runner()
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(gitops.GitError):
        gitops.git(repo, "status")


def test_big_output_and_exit_code_survive(runner):
    repo = runner()
    _commit(repo, "big.txt", "x" * 300_000 + "\n")
    assert len(gitops.git(repo, "show", "HEAD:big.txt")) == 300_000
    assert gitops._run(repo, "rev-parse", "--verify", "nope").returncode != 0


def _link_on_branch(repo, tmp_path, name, target):
    """Commit a symlink on ark/x, the way a worker's commit would bring it."""
    wt = tmp_path / "linker"
    gitops.fetch(repo)
    gitops.add_worktree(repo, wt, "ark/link", "origin/ark/x")
    (wt / "docs").mkdir(exist_ok=True)
    (wt / "docs" / "real.md").write_text("real\n")
    link = wt / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", "link"], check=True)
    gitops.push_ref(repo, gitops.rev(wt, "HEAD"), "ark/x")


@pytest.mark.parametrize("name,target,rel", [
    ("link.md", "OUT/victim.txt", "link.md"),                # file outside
    ("link.md", "OUT/not-yet.txt", "link.md"),               # dangling, would be created
    ("linkdir", "OUT/outdir", "linkdir/new.md"),             # directory outside
    ("linkdir", "OUT/outdir", "linkdir/deep/er/new.md"),     # below it, parents would be created
    ("linkdir", "OUT/missing", "linkdir/new.md"),            # dangling directory
    ("sub/linkdir", "OUT/outdir", "sub/linkdir/new.md"),     # deeper in the tree
    ("link.md", "docs/real.md", "link.md"),                  # inside: writes elsewhere, commits nothing
    ("linkdir", "docs", "linkdir/new.md"),                   # inside: writes elsewhere, git refuses after
])
def test_commit_file_never_writes_through_a_committed_symlink(runner, tmp_path, name, target, rel):
    repo = _branch_x(runner)
    out = tmp_path / "out"
    (out / "outdir").mkdir(parents=True)
    (out / "victim.txt").write_text("safe\n")
    _link_on_branch(repo, tmp_path, name, target.replace("OUT", str(out)))
    before = gitops.remote_sha(repo, "ark/x")
    scratch = tmp_path / "wt" / "s"
    with pytest.raises(gitops.GitError, match="symlink"):
        gitops.commit_file(repo, "ark/x", rel, "OVERWRITTEN\n", "m", scratch)
    assert (out / "victim.txt").read_text() == "safe\n"
    assert sorted(p.name for p in out.rglob("*")) == ["outdir", "victim.txt"]
    assert gitops.remote_sha(repo, "ark/x") == before
    assert not scratch.exists()


@pytest.mark.parametrize("rel", [".git", ".git/hooks/x", ".git/config", ".GIT", ".Git/config", "sub/.git/config",
                                 "", ".", "a/..", "a/../b.md", "a\0b"])
def test_commit_file_refuses_git_dir_and_non_paths(runner, tmp_path, rel):
    repo = _branch_x(runner)
    before = _origin_refs(repo)
    config = (repo / ".git" / "config").read_text()
    scratch = tmp_path / "wt" / "s"
    with pytest.raises(gitops.GitError):
        gitops.commit_file(repo, "ark/x", rel, "x\n", "m", scratch)
    assert _origin_refs(repo) == before
    assert (repo / ".git" / "config").read_text() == config
    assert not scratch.exists()
    gitops.remove_worktree(repo, scratch)
    assert gitops.git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("rel,content", [("README.md/x.md", "x\n"), ("adir", "x\n"), ("bad.md", "\udc80")])
def test_commit_file_write_failures_are_git_errors(runner, tmp_path, rel, content):
    repo = _branch_x(runner)
    wt = tmp_path / "w"
    gitops.fetch(repo)
    gitops.add_worktree(repo, wt, "ark/d", "origin/ark/x")
    _commit(wt, "adir/f.txt", "f\n")
    gitops.push_ref(repo, gitops.rev(wt, "HEAD"), "ark/x")
    before = gitops.remote_sha(repo, "ark/x")
    scratch = tmp_path / "wt" / "s"
    with pytest.raises(gitops.GitError):
        gitops.commit_file(repo, "ark/x", rel, content, "m", scratch)
    assert gitops.remote_sha(repo, "ark/x") == before
    assert not scratch.exists()


@pytest.mark.parametrize("rel", [":(top)x.md", ":/x.md", "*.md", "[a].md"])
def test_commit_file_takes_rel_literally_not_as_a_pathspec(runner, tmp_path, rel):
    repo = _branch_x(runner)
    before = gitops.remote_sha(repo, "ark/x")
    sha = gitops.commit_file(repo, "ark/x", rel, "x\n", "m", tmp_path / "wt" / "s")
    assert sha != before and gitops.remote_sha(repo, "ark/x") == sha
    gitops.fetch(repo)
    assert gitops.changed_files(repo, before, sha) == [rel]


# ---- minors from the same re-review ----

def test_remove_worktree_turns_os_errors_into_git_errors(runner, tmp_path):
    repo = runner()
    stale = tmp_path / "wt" / "stale"
    (stale / "ro").mkdir(parents=True)
    (stale / "ro" / "f").write_text("x")
    (stale / "ro").chmod(0o555)
    try:
        with pytest.raises(gitops.GitError):
            gitops.remove_worktree(repo, stale)
    finally:
        (stale / "ro").chmod(0o755)


def test_changed_files_keeps_whitespace_in_the_first_path(runner):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    _commit(repo, " src/x.txt", "x\n")
    _commit(repo, "zz.txt", "z\n")
    assert gitops.changed_files(repo, base, gitops.rev(repo, "HEAD")) == [" src/x.txt", "zz.txt"]


def test_changed_files_survives_a_non_utf8_path(runner):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    g = ["git", "-C", str(repo)]

    def run(*args, **kw):
        return subprocess.run([*g, *args], check=True, capture_output=True, **kw).stdout.decode().strip()

    blob = run("hash-object", "-w", "--stdin", input=b"x\n")
    run("update-index", "--add", "--cacheinfo", f"100644,{blob},".encode() + b"caf\xe9.txt")
    head = run("commit-tree", run("write-tree"), "-p", base, "-m", "m")
    run("read-tree", base)
    assert gitops.changed_files(repo, base, head) == [b"caf\xe9.txt".decode("utf-8", "surrogateescape")]


def test_add_worktree_after_its_directory_was_deleted(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    p = tmp_path / "wt" / "job"
    gitops.add_worktree(repo, p, "ark/a", base)
    shutil.rmtree(p)
    gitops.add_worktree(repo, p, "ark/a", base)
    assert gitops.rev(p, "HEAD") == base


# ---- N5: remove_worktree never deletes another repo's worktree ----

def test_remove_worktree_leaves_another_repos_live_worktree(runner, tmp_path):
    a, b = runner("a"), runner("b")
    wt = tmp_path / "wt" / "job"
    gitops.add_worktree(b, wt, "ark/x", gitops.rev(b, "HEAD"))
    (wt / "uncommitted.txt").write_text("keep me")
    with pytest.raises(gitops.GitError, match="job"):
        gitops.remove_worktree(a, wt)
    assert (wt / "uncommitted.txt").read_text() == "keep me"
    assert gitops.git(wt, "rev-parse", "--show-toplevel")


def test_remove_worktree_leaves_a_dir_with_a_git_directory(runner, tmp_path):
    repo = runner()
    d = tmp_path / "wt" / "clone"
    (d / ".git").mkdir(parents=True)
    (d / "f").write_text("x")
    with pytest.raises(gitops.GitError, match="clone"):
        gitops.remove_worktree(repo, d)
    assert (d / "f").exists()


def test_remove_worktree_removes_a_half_removed_scratch_of_this_repo(runner, tmp_path):
    repo = runner()
    wt = tmp_path / "wt" / "half"
    gitops.add_worktree(repo, wt, "ark/x", gitops.rev(repo, "HEAD"))
    (wt / "junk").write_text("x")
    # git forgot the worktree but the directory (with its .git file) is still there
    gitdir = (wt / ".git").read_text().split(":", 1)[1].strip()
    shutil.rmtree(gitdir)
    gitops.remove_worktree(repo, wt)
    assert not wt.exists()


def test_cleanup_refusal_does_not_discard_a_successful_squash(runner, tmp_path):
    a, b = runner("a"), runner("b")
    base = gitops.rev(a, "HEAD")
    w = tmp_path / "w"
    gitops.add_worktree(a, w, "ark/a", base)
    _commit(w, "a.txt", "a\n")
    scratch = tmp_path / "wt" / "s"
    # the scratch path becomes another repo's worktree only after squash_keep has run
    real = gitops.squash_keep

    def keep_then_swap(*args):
        sha = real(*args)
        gitops.git(a, "worktree", "remove", "--force", str(scratch))
        gitops.add_worktree(b, scratch, "ark/y", gitops.rev(b, "HEAD"))
        return sha

    import unittest.mock as m
    with m.patch.object(gitops, "squash_keep", keep_then_swap):
        assert gitops.squash(a, "master", "ark/a", "sq", scratch)
    assert scratch.exists()
