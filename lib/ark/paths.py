"""Where Arkimedes keeps things, and how a repo name maps to a folder."""
from __future__ import annotations

import copy
import errno
import json
import os
import stat
import subprocess
from pathlib import Path


def read_regular(path, limit: int = -1) -> bytes:
    """The bytes of a regular file, at most `limit`. Workers can write in a run folder: a FIFO there would
    hang the whole loop and a link could read anything, so whatever else is at `path` raises OSError."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(fd).st_mode):  # before fdopen: on a directory it raises and keeps the fd open
        os.close(fd)
        raise OSError(errno.EINVAL, "not a regular file", str(path))
    with os.fdopen(fd, "rb") as f:
        return f.read(limit)


def ark_home() -> Path:
    return Path(os.environ.get("ARK_HOME") or Path.home() / ".claude" / "arkimedes").expanduser()


def desk_dir() -> Path:
    return ark_home() / "desk"


def approvals_dir() -> Path:
    return ark_home() / "approvals"


def runs_dir() -> Path:
    return ark_home() / "runs"


def approval_file(repo: str, slug: str) -> Path:
    return approvals_dir() / repo / f"{slug}.json"


def run_file(repo: str, slug: str) -> Path:
    return runs_dir() / repo / f"{slug}.md"


DEFAULTS = {
    "roots": [],
    "exclude": [],
    "limits": {"writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1},
    "tick_minutes": 15,
    "poll_seconds": 15,
    "notify": [],
    "accounts": [],
}


def code_dir() -> Path:
    """Where Arkimedes' code lives (bin/, lib/, hooks/, agents/): this checkout."""
    return Path(__file__).resolve().parents[2]


def worktrees_dir() -> Path:
    return Path(os.environ.get("ARK_WORKTREES") or Path.home() / ".claude" / "ark-worktrees").expanduser()


def run_dir(repo: str, slug: str) -> Path:
    return runs_dir() / repo / slug


def load_workspace() -> dict:
    f = ark_home() / "workspace.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    ws = copy.deepcopy(DEFAULTS)
    ws.update({k: v for k, v in data.items() if k != "limits"})
    ws["limits"].update(data.get("limits") or {})
    return ws


def _has_origin(path: Path) -> bool:
    r = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def find_repo(name: str) -> Path | None:
    """`name` is the repo's path under a workspace root: "humanharness", "muscleapp/mobile" or
    "globallogic/proposals/v0".

    It must be a main checkout (`.git` is a folder, so linked worktrees never match) with an
    origin, at most two levels deeper than the root, and not excluded.
    """
    parts = Path(name).parts
    if not parts or len(parts) > 3 or ".." in parts or Path(name).is_absolute():
        return None
    ws = load_workspace()
    if name in ws.get("exclude", []):
        return None
    for root in ws.get("roots", []):
        cand = Path(root).expanduser() / name
        if (cand / ".git").is_dir() and _has_origin(cand):
            return cand
    return None


def repo_name(path) -> str | None:
    """The inverse of find_repo: the name of the repo whose main checkout is `path`, or None."""
    p = Path(path).expanduser().resolve()
    for root in load_workspace().get("roots", []):
        try:
            rel = p.relative_to(Path(root).expanduser().resolve())
        except ValueError:
            continue
        name = "/".join(rel.parts)
        found = find_repo(name) if name else None
        if found is not None and found.resolve() == p:
            return name
    return None
