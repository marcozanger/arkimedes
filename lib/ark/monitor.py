"""`ark runs --json` (control-center spec): everything the app shows, in one read-only pass.

The app reads only this, never runs/ itself, so the state format stays private to the runner."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import beat, desk, paths, run, timing
from .plan import load_plan, task_texts

VERSION = 1
MAX_ALERTS = 200
MAX_TEXT = 2_000_000  # bytes of a plan or decisions file sent to the page


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


def _num(v) -> float:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _text(path: Path) -> str:
    try:
        return path.read_bytes()[:MAX_TEXT].decode("utf-8", "replace")
    except OSError:
        return ""


def _title(text: str, slug: str) -> str:
    return next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), slug)


def _alerts() -> list[dict]:
    try:
        lines = (paths.runs_dir() / "alerts.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for n, line in enumerate(lines, 1):
        try:
            a = json.loads(line)
        except ValueError:
            continue
        if isinstance(a, dict):
            out.append({"id": n, "at": str(a.get("at") or ""), "text": str(a.get("text") or ""),
                        "critical": bool(a.get("critical")), "repo": str(a.get("repo") or ""),
                        "slug": str(a.get("slug") or ""), "job": str(a.get("job") or "")})
    return out[-MAX_ALERTS:]


def _approvals() -> dict[tuple[str, str], dict]:
    out = {}
    for f in sorted(paths.approvals_dir().glob("**/*.json")):
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(a, dict) and isinstance(a.get("repo"), str) and isinstance(a.get("slug"), str):
            out[(a["repo"], a["slug"])] = a
    return out


def _run_keys() -> set[tuple[str, str]]:
    root = paths.runs_dir()
    return {(str(f.parent.relative_to(root).parent), f.parent.name) for f in root.glob("**/state.json")}


def _empty(repo: str, slug: str) -> dict:
    return {"repo": repo, "slug": slug, "title": slug, "status": "waiting", "reason": "", "error": None,
            "approval": "-", "approved_at": None, "plan_path": "", "plan_sha": None, "spec": None, "created": None,
            "progress": {"merged": 0, "total": 0}, "times": None, "jobs": [], "events": [], "final_pr": None,
            "plan_file": "", "decisions": "", "manifest": {"setup": [], "integration_gate": "", "final_pr_title": ""}}


def _job(jid: str, mj: dict, sj: dict, heads: dict) -> dict:
    budget = mj.get("budget") if isinstance(mj.get("budget"), dict) else {}
    return {"id": jid, "status": sj.get("status", "pending"), "tier": sj.get("tier") or mj.get("tier", ""),
            "tier_path": sj.get("tier_path") or [mj.get("tier", "")], "attempts": sj.get("attempts", 0),
            "usd": _num(sj.get("usd")), "reason": sj.get("reason") or "", "question": sj.get("question") or "",
            "pr": sj.get("pr"), "started": sj.get("started"), "finished": sj.get("finished"),
            "after": list(mj.get("after") or []),
            "budget": {"minutes": _num(budget.get("minutes")), "usd": _num(budget.get("usd"))},
            "pr_title": mj.get("pr_title", ""), "tasks": [heads.get(t, t) for t in mj.get("tasks") or []],
            "includes": mj.get("includes", ""), "excludes": mj.get("excludes", ""), "done": list(mj.get("done") or []),
            "gate": mj.get("gate", ""), "heavy": bool(mj.get("heavy", False)), "area": list(mj.get("area") or [])}


def _plan(repo: str, slug: str, a: dict | None, now: datetime) -> dict:
    p = _empty(repo, slug)
    plan_path = Path(a["plan"]) if a and isinstance(a.get("plan"), str) else paths.desk_dir() / repo / slug / "plan.md"
    text = _text(plan_path)
    p.update(title=_title(text, slug), plan_path=str(plan_path), plan_file=text,
             decisions=_text(paths.run_dir(repo, slug) / "decisions.md"))
    if a:
        p.update(approval=desk._approval(repo, slug, plan_path), approved_at=a.get("approved_at"), plan_sha=a.get("sha256"))
    mjobs, heads = [], {}
    try:
        plan = load_plan(plan_path)
        p["spec"], p["created"] = plan.frontmatter.get("spec"), str(plan.frontmatter.get("created") or "") or None
        m = plan.manifest if isinstance(plan.manifest, dict) else {}
        p["manifest"] = {k: m.get(k, d) for k, d in (("setup", []), ("integration_gate", ""), ("final_pr_title", ""))}
        mjobs = [j for j in m.get("jobs") or [] if isinstance(j, dict) and isinstance(j.get("id"), str)]
        heads = {tid: t.splitlines()[0].lstrip("#").strip() for tid, t in task_texts(plan).items()}
    except Exception as e:  # a plan file that moved or broke must not hide its run
        p["error"] = f"cannot read the plan {plan_path}: {e}"
    st = run.load(repo, slug) or {}  # StateCorrupt goes up to _safe
    p.update(status=st.get("status", "waiting"), reason=st.get("reason") or "", events=list(st.get("events") or []),
             final_pr=st.get("final_pr"))
    sjobs = st.get("jobs") if isinstance(st.get("jobs"), dict) else {}
    by = {j["id"]: j for j in mjobs}
    ids = list(by) + [k for k in sjobs if k not in by]
    jobs = [_job(i, by.get(i, {}), sjobs.get(i) or {}, heads) for i in ids]
    t = timing.compute([{"id": j["id"], "after": j["after"], "minutes": j["budget"]["minutes"], "status": j["status"],
                         "started": j["started"], "finished": j["finished"]} for j in jobs], p["status"], now)
    for j in jobs:
        j.update({k: _iso(v) for k, v in t["jobs"][j["id"]].items()})
    p["jobs"] = jobs
    p["progress"] = {"merged": sum(j["status"] == "merged" for j in jobs), "total": len(jobs)}
    p["times"] = {"started": _iso(t["origin"]), "estimated_end": _iso(t["estimated_end"]),
                  "forecast_end": _iso(t["forecast_end"]), "delay_min": t["delay_min"], "left_min": t["left_min"]}
    return p


def _safe(repo: str, slug: str, a: dict | None, now: datetime) -> dict:
    try:
        return _plan(repo, slug, a, now)
    except Exception as e:  # one broken plan never hides the others
        p = _empty(repo, slug)
        p["error"] = str(e) if isinstance(e, run.StateCorrupt) else f"{type(e).__name__}: {e}"
        return p


def build(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    approvals = _approvals()
    keys = sorted(set(approvals) | _run_keys())
    return {"version": VERSION, "generated": _iso(now), "loop": beat.read(now.timestamp()), "alerts": _alerts(),
            "plans": [_safe(r, s, approvals.get((r, s)), now) for r, s in keys]}


def cmd_runs(args) -> int:
    if not args.json:
        print("ark runs es para la app: usá `ark runs --json`. Para leer vos: ark ps, ark status.", file=sys.stderr)
        return 2
    json.dump(build(), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def register(sub) -> None:
    s = sub.add_parser("runs", help="todo lo que muestra la app de control, en JSON (solo lectura)")
    s.add_argument("--json", action="store_true", help="la salida para la app")
    s.set_defaults(fn=cmd_runs)
