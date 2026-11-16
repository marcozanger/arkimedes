"""The desk: one folder per feature at desk/<repo>/<slug>/ with spec.md and/or plan.md."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from . import estimate, paths, run as runmod, versions
from .plan import PlanError, load_plan, sha256_file, split_frontmatter


@dataclass
class Feature:
    repo: str
    slug: str
    dir: Path
    spec: str      # spec status, "-" when there is no spec.md, "?" when unreadable
    plan: str      # plan status, same convention
    approval: str  # "-" none · "ok" sha matches · "stale" plan changed after approval
    run: str       # Status: line of runs/<repo>/<slug>.md, "-" when there is no run
    elapsed: str = "-"  # since the run started; frozen at its last update once it is not live
    eta: str = "-"      # "~<time>" left, "?" when a budget is missing, "-" when not live
    version: str = "-"  # "v<N>" of the last frozen version, "-" when never approved


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


LIVE = {"running", "waiting"}


def fmt_minutes(m: float) -> str:
    m = int(round(m))
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60:02d}m"


def _time(v):
    try:
        t = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _elapsed(state: dict | None, now: datetime) -> str:
    if not state:
        return "-"
    start = _time(state.get("started"))
    end = now if state.get("status") in LIVE else _time(state.get("updated"))
    if start is None or end is None:
        return "-"
    return fmt_minutes(max(0, (end - start).total_seconds() / 60))


def _eta(state: dict | None, manifest: dict | None, writers: int) -> str:
    if not state or state.get("status") not in LIVE:
        return "-"
    jobs = state.get("jobs") or {}
    todo = [j for j in (manifest or {}).get("jobs") or [] if (jobs.get(j.get("id")) or {}).get("status") != "merged"]
    if not todo:
        return "-"
    ids = {j.get("id") for j in todo}
    todo = [{**j, "after": [a for a in j.get("after") or [] if a in ids]} for j in todo]
    m = estimate.end_to_end(todo, writers)
    return "?" if m is None else "~" + fmt_minutes(m)


def _times(repo: str, slug: str, now: datetime) -> tuple[str, str]:
    try:
        state = runmod.load(repo, slug)
    except runmod.StateCorrupt:
        return "-", "-"
    if not state:
        return "-", "-"
    try:
        manifest = load_plan(Path(state["plan"])).manifest
        writers = int(paths.load_workspace()["limits"]["writers_per_repo"])
    except (PlanError, KeyError, ValueError, TypeError, OSError):
        manifest, writers = None, 1
    return _elapsed(state, now), (_eta(state, manifest, writers) if manifest else "?" if state.get("status") in LIVE else "-")


def _version(repo: str, slug: str) -> str:
    last = versions.latest(repo, slug)
    if last:
        return f"v{last['version']}"
    return "v1" if paths.approval_file(repo, slug).exists() else "-"


def features(now: datetime | None = None) -> list[Feature]:
    now = now or datetime.now(timezone.utc)
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
                           _approval(repo, slug, d / "plan.md"), _run(repo, slug),
                           *_times(repo, slug, now), _version(repo, slug)))
    return out
