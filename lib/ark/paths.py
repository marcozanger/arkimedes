"""Where Arkimedes keeps things, and how a repo name maps to a folder."""
from __future__ import annotations

import copy
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


DEFAULTS = {
    "roots": [],
    "exclude": [],
    "limits": {"writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1},
    "tick_minutes": 15,
    "poll_seconds": 15,
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
