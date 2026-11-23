"""git for the runner. It only ever pushes to ark/* branches (never the default branch, never force)."""
from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from . import paths

GIT_TIMEOUT = 300  # seconds; override per call with git(..., timeout=)
# For scratch worktrees: the target repo's hooks expect a checkout with its dependencies, and a
# signature can wait on a terminal. Neither has a say in the runner's own commits.
_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")
_NO_SIGN = ("-c", "commit.gpgsign=false")
_KILL_WAIT = 5  # seconds to wait for git to die after SIGKILL


class GitError(Exception):
    pass


class PathRefused(GitError):
    """The branch's own content will not take this path, so trying again cannot heal it."""


def _kill(p: subprocess.Popen) -> None:
    """SIGKILL git's process group, and wait for git a bounded time. Never raises: the caller is
    already reporting the timeout."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:  # group gone; on macOS also EPERM when it holds only the zombie leader
        pass
    try:
        p.kill()  # in case killpg did not reach it
        p.wait(timeout=_KILL_WAIT)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _text(f, errors: str) -> str:
    # pread: the file offset is shared with any process that outlived git and still holds the file
    return os.pread(f.fileno(), os.fstat(f.fileno()).st_size, 0).decode("utf-8", errors)


def _run(cwd, *args, timeout=None) -> subprocess.CompletedProcess:
    """git with no stdin, no terminal prompts and a timeout (the whole process group is killed).
    Output goes to files, not pipes: only git's own exit is waited for, so a daemon that a hook or
    helper left holding stdout or stderr can neither delay the result nor outlast the timeout.
    Paths in stdout that are not UTF-8 stay as surrogate escapes. Failing to run git is a GitError."""
    timeout = GIT_TIMEOUT if timeout is None else timeout
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            p = subprocess.Popen(["git", "-C", str(cwd), *args], stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=err, env=env, start_new_session=True)
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill(p)
                raise GitError(f"git {' '.join(args)}: timed out after {timeout}s") from None
            return subprocess.CompletedProcess(p.args, p.returncode, _text(out, "surrogateescape"),
                                               _text(err, "replace"))
    except OSError as e:
        raise GitError(f"git {' '.join(args)}: {e}") from e


def _checked(cwd, *args, check: bool = True, timeout=None) -> str:
    r = _run(cwd, *args, timeout=timeout)
    if check and r.returncode:
        raise GitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def git(cwd, *args, check: bool = True, timeout=None) -> str:
    return _checked(cwd, *args, check=check, timeout=timeout).strip()


def origin_url(repo) -> str:
    """origin's URL as configured. Not `git remote get-url`: that applies insteadOf, and the
    push layer in claude.runner_env matches the configured text."""
    return git(repo, "config", "--get", "remote.origin.url")


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


def is_ancestor(repo, ancestor: str, descendant: str) -> bool:
    """A commit this repo does not have is not an ancestor of anything."""
    return _run(repo, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


_ARK_BRANCH = re.compile(r"ark/[A-Za-z0-9][A-Za-z0-9._/-]*")


def _ark_only(branch: str) -> None:
    if (not _ARK_BRANCH.fullmatch(branch) or ".." in branch or "//" in branch
            or branch.endswith("/") or branch.endswith(".lock")):
        raise GitError(f"the runner only pushes plain ark/* branches, not {branch!r}")


def push_ref(repo, sha: str, branch: str) -> None:
    _ark_only(branch)
    # The shape alone is not enough: git reads a null sha as "delete the branch".
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha or "") or rev(repo, sha) != sha:
        raise GitError(f"push_ref needs the full sha of a commit, not {sha!r}")
    git(repo, "push", "-q", "origin", f"{sha}:refs/heads/{branch}")


def push_ref_lease(repo, sha: str, branch: str, expect: str) -> None:
    """Rewrite `branch` on origin to `sha`, only while origin still holds `expect` there. For a branch
    Euklides rebuilt; the old head must be saved first (a backup branch)."""
    _ark_only(branch)
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha or "") or rev(repo, sha) != sha:
        raise GitError(f"push_ref_lease needs the full sha of a commit, not {sha!r}")
    if not re.fullmatch(r"[0-9a-f]{40,64}", expect or ""):
        raise GitError(f"push_ref_lease needs the full sha origin is expected to hold, not {expect!r}")
    git(repo, "push", "-q", f"--force-with-lease=refs/heads/{branch}:{expect}", "origin",
        f"{sha}:refs/heads/{branch}")


def delete_branch(repo, branch: str) -> None:
    """Delete a local ark/* branch, merged or not: the sweep decided it carries nothing that exists nowhere else."""
    _ark_only(branch)
    git(repo, "branch", "-q", "-D", "--", branch)


def delete_remote(repo, branch: str) -> None:
    """Delete an ark/* branch on origin; nothing when it is not there."""
    _ark_only(branch)
    if remote_sha(repo, branch):
        delete_remote_branch(repo, branch)


def delete_remote_branch(repo, branch: str) -> None:
    _ark_only(branch)
    git(repo, "push", "-q", "origin", "--delete", f"refs/heads/{branch}")


def worktrees(repo) -> list[tuple[Path, str | None]]:
    """Every worktree git knows for this repo, the main checkout first: (path, branch), None for a detached HEAD."""
    out, path = [], None
    for line in _checked(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
            out.append((path, None))
        elif line.startswith("branch refs/heads/") and out:
            out[-1] = (path, line.removeprefix("branch refs/heads/"))
    return out


def branch_commits(worktree, base: str) -> list[dict]:
    """The commits of HEAD not in `base`, newest first: sha, subject and number of parents. Read-only."""
    out = _checked(worktree, "log", "--format=%H%x09%P%x09%s", "--end-of-options", f"{base}..HEAD")
    commits = []
    for line in out.splitlines():
        if line:
            sha, parents, subject = (line.split("\t", 2) + ["", ""])[:3]
            commits.append({"sha": sha, "subject": subject, "parents": len(parents.split())})
    return commits


def branch_changes(worktree, base: str) -> list[tuple[str, int | None, int | None]]:
    """(path, added, removed) for every file HEAD changed since it left `base`; None counts for a binary file."""
    out = _checked(worktree, "diff", "--no-renames", "--numstat", "-z", "--end-of-options", f"{base}...HEAD")
    changes = []
    for entry in out.split("\0"):
        if entry:
            added, removed, path = entry.split("\t", 2)
            changes.append((path, None, None) if added == "-" else (path, int(added), int(removed)))
    return changes


def current_branch(worktree) -> str | None:
    """The branch the worktree is on; None for a detached HEAD."""
    return git(worktree, "symbolic-ref", "--short", "-q", "HEAD", check=False) or None


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
    git(repo, "worktree", "prune", check=False)  # a registered worktree whose directory is gone blocks the add
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
        git(repo, "worktree", "add", "-q", str(path), branch)
    else:
        git(repo, "worktree", "add", "-q", "-b", branch, str(path), start)


def _belongs_elsewhere(repo, path: Path) -> bool:
    """True unless `path` is safe to delete: it has no .git entry (a stale scratch), or its .git is
    a file whose gitdir lies inside this repo's common git dir (a half-removed scratch of this
    repo). Anything else, including an unreadable .git, counts as another repo's."""
    dot_git = path / ".git"
    if not os.path.lexists(dot_git):
        return False
    try:
        if dot_git.is_symlink() or not dot_git.is_file():
            return True
        line = dot_git.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        if not line.startswith("gitdir:"):
            return True
        gitdir = Path(line.split(":", 1)[1].strip())
        gitdir = _abs(gitdir if gitdir.is_absolute() else path / gitdir)
        common = _abs(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        return common not in gitdir.parents
    except (OSError, IndexError, GitError):
        return True


def _make_removable(path: Path) -> None:
    """Give back the owner's permissions on every directory under `path`, which a gate may have
    left read-only. Never follows a symlink: its target is not ours to change."""
    def restore(d):
        try:
            os.chmod(d, os.stat(d, follow_symlinks=False).st_mode | stat.S_IRWXU)
        except OSError:
            pass  # the removal reports what is really in the way
    restore(path)
    for root, dirs, _ in os.walk(path):
        for name in dirs:
            d = os.path.join(root, name)
            if not os.path.islink(d):
                restore(d)


def remove_worktree(repo, path: Path) -> None:
    git(repo, "worktree", "remove", "--force", str(path), check=False)
    git(repo, "worktree", "prune", check=False)
    path = Path(path)
    if path.exists() or path.is_symlink():
        root = _abs(paths.worktrees_dir())
        if root in _abs(path).parents:
            if not path.is_symlink() and _belongs_elsewhere(repo, path):
                raise GitError(f"{path} holds a git checkout that is not a leftover of this repo: "
                               "not deleting it")
            try:
                if path.is_dir() and not path.is_symlink():
                    _make_removable(path)
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as e:
                raise GitError(f"cannot remove {path}: {e}") from e
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


def status_lines(worktree) -> list[str]:
    """`git status --porcelain`, one entry per non-empty line, unstripped (the status columns count). Read-only."""
    return [line for line in _checked(worktree, "status", "--porcelain").splitlines() if line.strip()]


def is_clean(worktree) -> bool:
    return git(worktree, "status", "--porcelain") == ""


def changed_files(repo, base: str, head: str) -> list[str]:
    """Every path touched between base and head, both sides of a rename, unquoted."""
    out = _checked(repo, "diff", "--no-renames", "--name-only", "-z", "--end-of-options", f"{base}...{head}")
    return [p for p in out.split("\0") if p]


def diff_text(repo, base: str, head: str, files: list[str]) -> str | None:
    """The unified diff of `files` between base and head with no context lines; None when git fails."""
    try:
        return _checked(repo, "diff", "--no-renames", "-U0", "--no-color", "--end-of-options", f"{base}...{head}",
                        "--", *files)
    except GitError:
        return None


def changed_lines(repo, base: str, head: str, files: list[str]) -> int | None:
    """Lines added plus removed in `files` between base and head; None when one of them is binary."""
    out = git(repo, "diff", "--no-renames", "--numstat", "--end-of-options", base, head, "--", *files)
    total = 0
    for line in out.splitlines():
        added, removed, _ = line.split("\t", 2)
        if added == "-" or removed == "-":
            return None
        total += int(added) + int(removed)
    return total


def squash_keep(repo, base_ref: str, head_ref: str, message: str, scratch: Path) -> str | None:
    """Squash `head_ref` onto `base_ref` in a detached scratch worktree and commit; the new sha,
    or None on a real conflict (unmerged paths). Any other merge failure raises GitError.
    The scratch worktree stays: the caller must remove_worktree() it."""
    scratch = Path(scratch)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, *_NO_HOOKS, "worktree", "add", "-q", "--detach", str(scratch), base_ref)
    r = _run(scratch, *_NO_HOOKS, "-c", "merge.ff=true", "merge", "--squash", head_ref)
    if r.returncode:
        if git(scratch, "ls-files", "-u"):
            return None
        raise GitError(f"git merge --squash {head_ref}: {(r.stderr or r.stdout).strip()}")
    if _run(scratch, "diff", "--cached", "--quiet").returncode:
        git(scratch, *_NO_HOOKS, *_NO_SIGN, "commit", "-q", "-m", message)
    return rev(scratch, "HEAD")


def merge_keep(repo, base_ref: str, main_ref: str, message: str, scratch: Path) -> tuple[str | None, list[str]]:
    """Merge `main_ref` into `base_ref` (two parents, never a fast-forward of the base onto main) in a detached
    scratch worktree: (new sha, []) or (None, conflicting paths). When main is already in the base, the base
    itself. Any other merge failure raises GitError. The scratch worktree stays: the caller removes it."""
    scratch = Path(scratch)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, *_NO_HOOKS, "worktree", "add", "-q", "--detach", str(scratch), base_ref)
    if is_ancestor(repo, main_ref, base_ref):
        return rev(scratch, "HEAD"), []
    # commit.gpgsign is read by the merge commit too, so _NO_SIGN covers it.
    r = _run(scratch, *_NO_HOOKS, *_NO_SIGN, "merge", "--no-ff", "--no-edit", "-m", message, main_ref)
    if r.returncode:
        unmerged = sorted({line.split("\t", 1)[1] for line in git(scratch, "ls-files", "-u").splitlines()
                           if "\t" in line})
        if unmerged:
            _run(scratch, "merge", "--abort")
            return None, unmerged
        raise GitError(f"git merge {main_ref}: {(r.stderr or r.stdout).strip()}")
    return rev(scratch, "HEAD"), []


def squash(repo, base_ref: str, head_ref: str, message: str, scratch: Path) -> str | None:
    """squash_keep, then remove the scratch worktree; nothing is pushed."""
    try:
        return squash_keep(repo, base_ref, head_ref, message, scratch)
    finally:
        _cleanup(repo, scratch)


def _check_rel(rel: str) -> None:
    """A plain relative path, judged by its text alone: nothing above it, nothing in a .git."""
    parts = PurePosixPath(rel).parts
    if (not parts or PurePosixPath(rel).is_absolute() or "\0" in rel or ".." in parts
            or any(p.casefold() == ".git" for p in parts)):  # casefold: .GIT is .git on macOS
        raise GitError(f"{rel!r} is not a plain path inside the scratch worktree")


def _refuse_symlinks(scratch: Path, rel: str) -> None:
    """The branch's content comes from workers. A symlink anywhere along `rel` would send the write
    elsewhere, outside the repo or to another file, and git would commit nothing of it."""
    here = scratch
    for part in PurePosixPath(rel).parts:
        here = here / part
        if here.is_symlink():
            raise PathRefused(f"{rel!r} goes through the symlink {part!r} on the branch")


def commit_file(repo, branch: str, rel: str, content: str, message: str, scratch: Path) -> str:
    """Commit one file on top of origin/<branch> and push it there. When the file is already
    that content on the branch (a retry after a crash), nothing is committed or pushed."""
    _ark_only(branch)
    scratch = Path(scratch)
    _check_rel(rel)
    fetch(repo)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, *_NO_HOOKS, "worktree", "add", "-q", "--detach", str(scratch), f"origin/{branch}")
    try:
        _refuse_symlinks(scratch, rel)
        f = scratch / rel
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
        except (FileExistsError, NotADirectoryError, IsADirectoryError) as e:  # a file or directory in the way
            raise PathRefused(f"cannot write {rel!r}: {e}") from e
        except (OSError, ValueError) as e:  # a full disk; content that is not text
            raise GitError(f"cannot write {rel!r}: {e}") from e
        try:
            git(scratch, "--literal-pathspecs", "add", "--", rel)
        except GitError as e:
            if _run(scratch, "check-ignore", "-q", "--no-index", "--", rel).returncode == 0:
                raise PathRefused(f"{rel!r} is ignored on the branch: {e}") from None
            raise
        if _run(scratch, "diff", "--cached", "--quiet").returncode:
            git(scratch, *_NO_HOOKS, *_NO_SIGN, "commit", "-q", "-m", message)
            push_branch(scratch, branch)
        return rev(scratch, "HEAD")
    finally:
        _cleanup(repo, scratch)
