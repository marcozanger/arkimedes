"""git for the runner. It only ever pushes to ark/* branches (never the default branch, never force)."""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def git(cwd, *args, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise GitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def fetch(repo) -> None:
    git(repo, "fetch", "-q", "--prune", "origin")


def default_branch(repo) -> str:
    """The branch origin/HEAD points to (master or main)."""
    for attempt in range(2):
        ref = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
        if ref.startswith("origin/"):
            return ref.removeprefix("origin/")
        if attempt == 0:
            git(repo, "remote", "set-head", "origin", "--auto", check=False)
    raise GitError("cannot tell the default branch: origin/HEAD is not set")


def rev(cwd, ref: str) -> str:
    return git(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")


def remote_sha(repo, branch: str) -> str | None:
    out = git(repo, "ls-remote", "origin", f"refs/heads/{branch}")
    return out.split()[0] if out else None


def _ark_only(branch: str) -> None:
    if not branch.startswith("ark/"):
        raise GitError(f"the runner only pushes ark/* branches, not {branch!r}")


def push_ref(repo, sha: str, branch: str) -> None:
    _ark_only(branch)
    git(repo, "push", "-q", "origin", f"{sha}:refs/heads/{branch}")


def push_branch(worktree, branch: str) -> None:
    _ark_only(branch)
    git(worktree, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")


def add_worktree(repo, path: Path, branch: str, start: str) -> None:
    """A worktree on `branch`; the branch is created at `start` when it does not exist yet."""
    path = Path(path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
        git(repo, "worktree", "add", "-q", str(path), branch)
    else:
        git(repo, "worktree", "add", "-q", "-b", branch, str(path), start)


def remove_worktree(repo, path: Path) -> None:
    git(repo, "worktree", "remove", "--force", str(path), check=False)
    git(repo, "worktree", "prune", check=False)


def is_clean(worktree) -> bool:
    return git(worktree, "status", "--porcelain") == ""


def changed_files(repo, base: str, head: str) -> list[str]:
    out = git(repo, "diff", "--name-only", f"{base}...{head}")
    return [line for line in out.splitlines() if line]


def squash_keep(repo, base_ref: str, head_ref: str, message: str, scratch: Path) -> str | None:
    """Squash `head_ref` onto `base_ref` in a detached scratch worktree and commit; the new sha,
    or None on a conflict. The scratch worktree stays: the caller must remove_worktree() it."""
    scratch = Path(scratch)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--detach", str(scratch), base_ref)
    r = subprocess.run(["git", "-C", str(scratch), "merge", "--squash", head_ref],
                       capture_output=True, text=True)
    if r.returncode:
        return None
    if subprocess.run(["git", "-C", str(scratch), "diff", "--cached", "--quiet"]).returncode:
        git(scratch, "commit", "-q", "-m", message)
    return rev(scratch, "HEAD")


def squash(repo, base_ref: str, head_ref: str, message: str, scratch: Path) -> str | None:
    """squash_keep, then remove the scratch worktree; nothing is pushed."""
    try:
        return squash_keep(repo, base_ref, head_ref, message, scratch)
    finally:
        remove_worktree(repo, scratch)


def commit_file(repo, branch: str, rel: str, content: str, message: str, scratch: Path) -> str:
    """Commit one file on top of origin/<branch> and push it there."""
    _ark_only(branch)
    fetch(repo)
    scratch = Path(scratch)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--detach", str(scratch), f"origin/{branch}")
    try:
        f = scratch / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        git(scratch, "add", rel)
        git(scratch, "commit", "-q", "-m", message)
        push_branch(scratch, branch)
        return rev(scratch, "HEAD")
    finally:
        remove_worktree(repo, scratch)
