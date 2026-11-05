"""The desk: one folder per feature at desk/<repo>/<slug>/ with spec.md and/or plan.md."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .plan import PlanError, sha256_file, split_frontmatter


@dataclass
class Feature:
    repo: str
    slug: str
    dir: Path
    spec: str      # spec status, "-" when there is no spec.md, "?" when unreadable
    plan: str      # plan status, same convention
    approval: str  # "-" none · "ok" sha matches · "stale" plan changed after approval
    run: str       # Status: line of runs/<repo>/<slug>.md, "-" when there is no run


def _status(f: Path) -> str:
    if not f.exists():
        return "-"
    try:
        return str(split_frontmatter(f.read_text(encoding="utf-8"))[0].get("status") or "?")
    except (PlanError, OSError):
        return "?"


def _approval(repo: str, slug: str, plan_file: Path) -> str:
    a = paths.approval_file(repo, slug)
    if not a.exists():
        return "-"
    try:
        rec = json.loads(a.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "?"
    if not plan_file.exists():
        return "stale"
    return "ok" if rec.get("sha256") == sha256_file(plan_file) else "stale"


def _run(repo: str, slug: str) -> str:
    f = paths.run_file(repo, slug)
    if not f.exists():
        return "-"
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.startswith("Status:"):
            return line.split(":", 1)[1].strip() or "?"
    return "?"


def features() -> list[Feature]:
    root = paths.desk_dir()
    if not root.is_dir():
        return []
    dirs = sorted({f.parent for name in ("spec.md", "plan.md") for f in root.rglob(name)})
    out: list[Feature] = []
    for d in dirs:
        rel = d.relative_to(root)
        if len(rel.parts) < 2:
            continue
        repo, slug = "/".join(rel.parts[:-1]), rel.parts[-1]
        out.append(Feature(repo, slug, d, _status(d / "spec.md"), _status(d / "plan.md"),
                           _approval(repo, slug, d / "plan.md"), _run(repo, slug)))
    return out
