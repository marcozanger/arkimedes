"""git for the runner. It only ever pushes to ark/* branches (never the default branch, never force)."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from pathlib import Path

from . import paths

GIT_TIMEOUT = 300  # seconds; override per call with git(..., timeout=)


class GitError(Exception):
    pass


def _run(cwd, *args, timeout=None) -> subprocess.CompletedProcess:
    """git with no stdin, no terminal prompts and a timeout (the whole process group is killed)."""
    timeout = GIT_TIMEOUT if timeout is None else timeout
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    p = subprocess.Popen(["git", "-C", str(cwd), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        raise GitError(f"git {' '.join(args)}: timed out after {timeout}s") from None
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def git(cwd, *args, check: bool = True, timeout=None) -> str:
    r = _run(cwd, *args, timeout=timeout)
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
    want = f"refs/heads/{branch}"
    for line in git(repo, "ls-remote", "origin", want).splitlines():
        sha, _, ref = line.partition("\t")
        if ref == want:
            return sha
    return None


_ARK_BRANCH = re.compile(r"ark/[A-Za-z0-9][A-Za-z0-9._/-]*")


def _ark_only(branch: str) -> None:
    if (not _ARK_BRANCH.fullmatch(branch) or ".." in branch or "//" in branch
            or branch.endswith("/") or branch.endswith(".lock")):
        raise GitError(f"the runner only pushes plain ark/* branches, not {branch!r}")


def push_ref(repo, sha: str, branch: str) -> None:
    _ark_only(branch)
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha or ""):
        raise GitError(f"push_ref needs a full commit sha, not {sha!r}")
    git(repo, "push", "-q", "origin", f"{sha}:refs/heads/{branch}")


def push_branch(worktree, branch: str) -> None:
    _ark_only(branch)
    git(worktree, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")


def _abs(p) -> Path:
    return Path(p).resolve()


def add_worktree(repo, path: Path, branch: str, start: str) -> None:
    """A worktree on `branch`; the branch is created at `start` when it does not exist yet.
    An existing path is accepted only when it is that repo's worktree, on that branch (resume)."""
    path = Path(path)
    if path.exists():
        try:
            top = git(path, "rev-parse", "--show-toplevel")
            common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
            mine = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
            on = git(path, "symbolic-ref", "--short", "HEAD", check=False)
        except GitError as e:
            raise GitError(f"{path} exists and is not a usable worktree: {e}") from None
        if _abs(top) != _abs(path) or _abs(common) != _abs(mine) or on != branch:
            raise GitError(f"{path} exists but is not a worktree of this repo on {branch!r}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
        git(repo, "worktree", "add", "-q", str(path), branch)
    else:
        git(repo, "worktree", "add", "-q", "-b", branch, str(path), start)


def remove_worktree(repo, path: Path) -> None:
    git(repo, "worktree", "remove", "--force", str(path), check=False)
    git(repo, "worktree", "prune", check=False)
    path = Path(path)
    if path.exists() or path.is_symlink():
        root = _abs(paths.worktrees_dir())
        if root in _abs(path).parents:
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
            git(repo, "worktree", "prune", check=False)
        else:
            raise GitError(f"{path} is left over and is outside {root}: remove it by hand")


def _cleanup(repo, path: Path) -> None:
    """remove_worktree in a finally block: a cleanup error must not replace the real outcome (the
    next call removes a leftover first and raises there)."""
    try:
        remove_worktree(repo, path)
    except (GitError, OSError):
        pass


def is_clean(worktree) -> bool:
    return git(worktree, "status", "--porcelain") == ""


def changed_files(repo, base: str, head: str) -> list[str]:
    """Every path touched between base and head, both sides of a rename, unquoted."""
    out = git(repo, "diff", "--no-renames", "--name-only", "-z", "--end-of-options", f"{base}...{head}")
    return [p for p in out.split("\0") if p]


def squash_keep(repo, base_ref: str, head_ref: str, message: str, scratch: Path) -> str | None:
    """Squash `head_ref` onto `base_ref` in a detached scratch worktree and commit; the new sha,
    or None on a real conflict (unmerged paths). Any other merge failure raises GitError.
    The scratch worktree stays: the caller must remove_worktree() it."""
    scratch = Path(scratch)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--detach", str(scratch), base_ref)
    r = _run(scratch, "-c", "merge.ff=true", "merge", "--squash", head_ref)
    if r.returncode:
        if git(scratch, "ls-files", "-u"):
            return None
        raise GitError(f"git merge --squash {head_ref}: {(r.stderr or r.stdout).strip()}")
    if _run(scratch, "diff", "--cached", "--quiet").returncode:
        git(scratch, "commit", "-q", "-m", message)
    return rev(scratch, "HEAD")


def squash(repo, base_ref: str, head_ref: str, message: str, scratch: Path) -> str | None:
    """squash_keep, then remove the scratch worktree; nothing is pushed."""
    try:
        return squash_keep(repo, base_ref, head_ref, message, scratch)
    finally:
        _cleanup(repo, scratch)


def commit_file(repo, branch: str, rel: str, content: str, message: str, scratch: Path) -> str:
    """Commit one file on top of origin/<branch> and push it there. When the file is already
    that content on the branch (a retry after a crash), nothing is committed or pushed."""
    _ark_only(branch)
    scratch = Path(scratch)
    if not rel or _abs(scratch) not in _abs(scratch / rel).parents:
        raise GitError(f"{rel!r} is not a path inside the scratch worktree")
    fetch(repo)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--detach", str(scratch), f"origin/{branch}")
    try:
        f = scratch / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        git(scratch, "add", "--", rel)
        if _run(scratch, "diff", "--cached", "--quiet").returncode:
            git(scratch, "commit", "-q", "-m", message)
            push_branch(scratch, branch)
        return rev(scratch, "HEAD")
    finally:
        _cleanup(repo, scratch)
