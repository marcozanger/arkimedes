"""The drafts the control center lists: features on the desk whose plan has no `ok` approval."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import desk, estimate, paths, reviews, validate
from .plan import PlanError, load_plan, split_frontmatter

REPO = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_IMAGE = 20_000_000


class DraftError(Exception):
    pass


def folder(repo: str, slug: str) -> Path:
    if not (isinstance(repo, str) and isinstance(slug, str) and REPO.match(repo) and SLUG.match(slug)):
        raise DraftError("bad repo or slug")
    if any(p in ("..", ".") for p in repo.split("/")):
        raise DraftError("bad repo or slug")
    root = paths.desk_dir().resolve()
    d = (root / repo / slug).resolve()
    if not d.is_relative_to(root) or not d.is_dir():
        raise DraftError(f"no draft {repo}/{slug}")
    return d


def _title(d: Path) -> str:
    for name in ("spec.md", "plan.md"):
        try:
            for line in (d / name).read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("# "):
                    return line[2:].strip()
        except OSError:
            pass
    return d.name


def _updated(d: Path) -> float:
    ts = []
    for n in ("spec.md", "plan.md", "review.json"):
        try:
            ts.append((d / n).stat().st_mtime)
        except OSError:
            pass
    return max(ts, default=0)


def _doc(d: Path, name: str, doc: str) -> dict | None:
    f = d / name
    if not f.exists():
        return None
    try:
        status = str(split_frontmatter(f.read_text(encoding="utf-8"))[0].get("status") or "?")
    except (PlanError, OSError):
        status = "?"
    return {"status": status, "review": reviews.summary(d, doc)}


def _plan_extra(d: Path, out: dict) -> None:
    try:
        out["problems"] = [str(p) for p in validate.validate(d / "plan.md")]
    except Exception as e:
        out["problems"] = [f"cannot validate: {e}"]
    out["estimate"] = None
    if not out["problems"]:
        try:
            writers = int(paths.load_workspace()["limits"]["writers_per_repo"])
        except (KeyError, ValueError, TypeError):
            writers = 1
        try:
            e = estimate.estimate(load_plan(d / "plan.md").manifest, writers)
            out["estimate"] = {k: e[k] for k in ("total_usd", "total_minutes", "end_to_end_minutes")}
        except Exception:
            pass


def _next(spec: dict | None, plan: dict | None) -> str:
    if spec is not None and spec["status"] != "approved":
        r = spec["review"]
        return "approve spec" if r["total"] and r["ok"] == r["total"] else "review spec"
    if plan is None:
        return "write plan"
    if plan["problems"]:
        return "fix plan"
    r = plan["review"]
    return "approve plan" if r["total"] and r["ok"] == r["total"] else "review plan"


def _entry(f) -> dict:
    d = f.dir
    spec, plan = _doc(d, "spec.md", "spec"), _doc(d, "plan.md", "plan")
    if plan is not None:
        _plan_extra(d, plan)
    return {"repo": f.repo, "slug": f.slug, "title": _title(d), "spec": spec, "plan": plan,
            "approval": f.approval, "updated": _updated(d), "next": _next(spec, plan)}


def list_drafts() -> list[dict]:
    return [_entry(f) for f in desk.features() if f.approval != "ok"]


def detail(repo: str, slug: str) -> dict:
    d = folder(repo, slug)
    f = next((x for x in desk.features() if (x.repo, x.slug) == (repo, slug)), None)
    if f is None:
        raise DraftError(f"no draft {repo}/{slug}")
    out = _entry(f)
    for key, name in (("spec", "spec.md"), ("plan", "plan.md")):
        if out[key] is not None:
            text = (d / name).read_text(encoding="utf-8", errors="replace")
            out[key].update(text=text, sha256=hashlib.sha256((d / name).read_bytes()).hexdigest(),
                            review=reviews.load(d, key))
    return out


def images(repo: str, slug: str, name: str) -> bytes:
    d = folder(repo, slug)
    if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith(".") \
            or Path(name).suffix.lower() not in IMAGES:
        raise DraftError("not an image of this draft")
    f = d / name
    if f.is_symlink() or not f.is_file():
        raise DraftError("no such image")
    try:
        return paths.read_regular(f, MAX_IMAGE)
    except OSError as e:
        raise DraftError(str(e)) from e
