"""Plan versions. Every approval freezes the closed plan and its spec as v<N> under
approvals/<repo>/<slug>/, and versions.jsonl lists them. Nothing here ends in .json: the approval
globs (loop.approvals, monitor) read every approvals/**/*.json as an approval."""
from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import yaml

from . import paths
from .plan import load_plan, sha256_file, task_texts

LEGACY_PLAN, LEGACY_SPEC = "v1.legacy.md", "v1.legacy.spec.md"


def folder(repo: str, slug: str) -> Path:
    return paths.approvals_dir() / repo / slug


def version_dir(repo: str, slug: str, n: int) -> Path:
    return folder(repo, slug) / f"v{n}"


def versions(repo: str, slug: str) -> list[dict]:
    f = folder(repo, slug) / "versions.jsonl"
    out = []
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                v = json.loads(line)
            except ValueError:
                continue
            if isinstance(v, dict) and type(v.get("version")) is int:
                out.append(v)
    return sorted(out, key=lambda v: v["version"])


def latest(repo: str, slug: str) -> dict | None:
    vs = versions(repo, slug)
    return vs[-1] if vs else None


def by_sha(repo: str, slug: str, sha: str) -> dict | None:
    """The newest version whose plan has this sha256, or None."""
    for v in reversed(versions(repo, slug)):
        if v.get("sha256") == sha:
            return v
    return None


def plan_path(repo: str, slug: str, n: int) -> Path | None:
    p = version_dir(repo, slug, n) / "plan.md"
    return p if p.is_file() else None


def text(repo: str, slug: str, n: int, kind: str = "plan") -> str | None:
    p = version_dir(repo, slug, n) / f"{kind}.md"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def _append(repo: str, slug: str, rec: dict) -> None:
    f = folder(repo, slug) / "versions.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def _copy(repo: str, slug: str, n: int, plan_src: Path, spec_src: Path | None) -> dict:
    d = version_dir(repo, slug, n)
    d.mkdir(parents=True, exist_ok=False)  # a version is never written twice
    shutil.copyfile(plan_src, d / "plan.md")
    spec_sha = None
    if spec_src is not None and spec_src.is_file():
        shutil.copyfile(spec_src, d / "spec.md")
        spec_sha = sha256_file(d / "spec.md")
    return {"sha256": sha256_file(d / "plan.md"), "spec_sha": spec_sha}


def _legacy(repo: str, slug: str, desk: Path) -> dict | None:
    """A pre-versions approval as v1: its text when `ark replan` kept a copy that still matches it."""
    f = paths.approval_file(repo, slug)
    try:
        old = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(old, dict) or "version" in old or not isinstance(old.get("sha256"), str):
        return None
    rec = {"version": 1, "sha256": old["sha256"], "spec_sha": None,
           "approved_at": str(old.get("approved_at", "")), "kept": False}
    kept = desk / LEGACY_PLAN
    if kept.is_file() and sha256_file(kept) == old["sha256"]:
        spec = desk / LEGACY_SPEC
        rec.update(_copy(repo, slug, 1, kept, spec if spec.is_file() else desk / "spec.md"), kept=True)
    _append(repo, slug, rec)
    return rec


def freeze(plan_file, approved_at: str) -> dict:
    """Copy the closed plan and its spec as the next version. Returns the version record plus `plan`,
    the snapshot's absolute path (what the approval and the run point at)."""
    plan_file = Path(plan_file)
    fm = load_plan(plan_file).frontmatter
    repo, slug = str(fm["repo"]), str(fm["slug"])
    vs = versions(repo, slug)
    if not vs:
        legacy = _legacy(repo, slug, plan_file.parent)
        vs = [legacy] if legacy else []
    n = (vs[-1]["version"] if vs else 0) + 1
    rec = {"version": n, **_copy(repo, slug, n, plan_file, plan_file.parent / "spec.md"),
           "approved_at": approved_at, "kept": True}
    _append(repo, slug, rec)
    return {**rec, "plan": str((version_dir(repo, slug, n) / "plan.md").resolve())}


def _jobs(plan_file) -> dict[str, str]:
    """Each job's manifest entry and task texts, as one comparable text."""
    plan = load_plan(Path(plan_file))
    texts = task_texts(plan)
    out = {}
    for mj in plan.manifest["jobs"]:
        body = yaml.safe_dump(mj, sort_keys=True)
        body += "\n".join(texts.get(t, "") for t in mj["tasks"])
        out[mj["id"]] = body
    return out


def job_changes(old_plan, new_plan) -> dict[str, str]:
    """id -> unchanged | changed | added | removed. With no old text (None), every job of the new
    version is `changed`: nothing kept can show it is the same work."""
    new = _jobs(new_plan)
    if old_plan is None:
        return {j: "changed" for j in new}
    old = _jobs(old_plan)
    out = {}
    for j in new:
        out[j] = "added" if j not in old else ("unchanged" if old[j] == new[j] else "changed")
    for j in old:
        if j not in new:
            out[j] = "removed"
    return out


def _udiff(a: str | None, b: str | None, name: str, va: str, vb: str) -> str:
    lines = difflib.unified_diff((a or "").splitlines(), (b or "").splitlines(),
                                 f"{name} {va}", f"{name} {vb}", lineterm="")
    return "\n".join(lines)


def diff(repo: str, slug: str, a: int, b: int) -> str:
    """The spec diff first, then the plan diff, between versions a and b."""
    parts = []
    for kind in ("spec", "plan"):
        d = _udiff(text(repo, slug, a, kind), text(repo, slug, b, kind), f"{kind}.md", f"v{a}", f"v{b}")
        parts.append(d or f"{kind}.md: no changes between v{a} and v{b}")
    return "\n\n".join(parts) + "\n"


def job_diff(old_plan, new_plan, job_id: str) -> str:
    """What changed in one job between two plan files: its manifest entry and its task texts."""
    old = _jobs(old_plan).get(job_id) if old_plan else None
    new = _jobs(new_plan).get(job_id)
    return _udiff(old, new, f"job {job_id}", "before", "after")


def by_version(rows: list[dict], timeline: list[dict]) -> list[dict]:
    """Each version's cost and working minutes: a step belongs to the newest version approved before
    it started. Steps with no start count for the first version."""
    tl = sorted(timeline, key=lambda v: v["version"])
    out = [{"version": v["version"], "approved_at": v["approved_at"], "usd": 0.0, "minutes": 0.0} for v in tl]
    for r in rows if out else []:
        start = r.get("start") or ""
        i = 0
        for k, v in enumerate(tl):
            if start and start >= v["approved_at"]:
                i = k
        out[i]["usd"] = round(out[i]["usd"] + (r.get("usd") or 0), 4)
        out[i]["minutes"] = round(out[i]["minutes"] + (r.get("minutes") or 0), 2)
    return out
