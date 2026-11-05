"""Where Arkimedes keeps things, and how a repo name maps to a folder."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


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


def load_workspace() -> dict:
    f = ark_home() / "workspace.json"
    if not f.exists():
        return {"roots": [], "exclude": []}
    return json.loads(f.read_text(encoding="utf-8"))


def _has_origin(path: Path) -> bool:
    r = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def find_repo(name: str) -> Path | None:
    """`name` is the repo's path under a workspace root: "humanharness" or "muscleapp/mobile".

    It must be a main checkout (`.git` is a folder, so linked worktrees never match) with an
    origin, at most one level deeper than the root, and not excluded.
    """
    parts = Path(name).parts
    if not parts or len(parts) > 2 or ".." in parts or Path(name).is_absolute():
        return None
    ws = load_workspace()
    if name in ws.get("exclude", []):
        return None
    for root in ws.get("roots", []):
        cand = Path(root).expanduser() / name
        if (cand / ".git").is_dir() and _has_origin(cand):
            return cand
    return None
