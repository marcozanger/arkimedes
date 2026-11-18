"""The numbers of one run, from its state, its steps and its plan's jobs. No I/O: the report, the PR summary
and the history all read the same dict."""
from __future__ import annotations

from . import estimate, paths, usage, versions
from .steps import num, ts

NOT_PROCESSES = ("merge", "wait-marco")


def _working(rows) -> float:
    """Minutes during which at least one of these steps ran: parallel steps count once."""
    spans = sorted((ts(r["start"]), ts(r["end"])) for r in rows if r["start"] and r["end"])
    total, cur_s, cur_e = 0.0, None, None
    for s, e in spans:
        if s is None or e is None:
            continue
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return round(total / 60, 2)


def _sum(rows, key) -> float:
    return round(sum(num(r[key]) or 0 for r in rows), 4)


def _group(rows, key) -> dict:
    out: dict = {}
    for r in rows:
        g = out.setdefault(r[key] or "?", {"n": 0, "minutes": 0.0, "usd": 0.0, "tokens": 0})
        g["n"] += 1
        g["minutes"] = round(g["minutes"] + (r["minutes"] or 0), 2)
        g["usd"] = round(g["usd"] + (r["usd"] or 0), 4)
        g["tokens"] += r["tokens_total"] or 0
    return out


def _budget(mjobs: dict, key: str):
    values = [num(((mj or {}).get("budget") or {}).get(key)) for mj in mjobs.values()]
    return sum(values) if values and None not in values else None


def summarize(st: dict, rows: list[dict], mjobs: dict) -> dict:
    procs = [r for r in rows if r["kind"] not in NOT_PROCESSES]
    jobs = {}
    for jid, j in st["jobs"].items():
        mine = [r for r in procs if r["job"] == jid]
        b = (mjobs.get(jid) or {}).get("budget") or {}
        jobs[jid] = {
            "status": j.get("status"), "tiers": list(j.get("tier_path") or [j.get("tier")]),
            "attempts": sum(r["kind"] == "worker" for r in mine),
            "reviews": sum(r["kind"] == "review" for r in mine),
            "gate_fails": sum(r["kind"] == "gate" and r["outcome"] == "red" for r in mine),
            "steers": int(num(j.get("steers")) or 0), "integrate_fails": int(num(j.get("integrate_fails")) or 0),
            "minutes": _working(mine), "usd": _sum(mine, "usd"), "tokens": int(_sum(mine, "tokens_total")),
            "budget_minutes": num(b.get("minutes")), "budget_usd": num(b.get("usd")),
            "pr": ((j.get("pr") or {}).get("url")), "reason": usage.clean(str(j.get("reason") or ""))[:500],
            "estimated": any(r["estimated"] for r in mine)}
    started, ended = ts(st.get("started")), ts(st.get("updated"))
    statuses = [j["status"] for j in jobs.values()]
    tl = sorted(st.get("versions") or [], key=lambda x: x["version"])
    return {
        "repo": st["repo"], "slug": st["slug"], "status": st.get("status"),
        "started": st.get("started"), "ended": st.get("updated"),
        "wall_minutes": round((ended - started) / 60, 2) if started and ended and ended >= started else None,
        "estimated_minutes": estimate.end_to_end(list(mjobs.values()),
                                                 int(paths.load_workspace()["limits"]["writers_per_repo"])),
        "working_minutes": _working(procs),
        "waiting_minutes": round(sum(r["minutes"] or 0 for r in rows if r["kind"] == "wait-marco"), 2),
        "usd": _sum(procs, "usd"), "tokens": int(_sum(procs, "tokens_total")),
        "merged": statuses.count("merged"), "blocked": statuses.count("blocked"),
        "integrate_fails": sum(j["integrate_fails"] for j in jobs.values()),
        "budget_usd": _budget(mjobs, "usd"), "budget_minutes": _budget(mjobs, "minutes"),
        "final_pr": (st.get("final_pr") or {}).get("url"),
        "estimated": any(r["estimated"] for r in procs),
        "flaky": list(st.get("flaky") or []), "queue_error": st.get("queue_error"),
        "versions": [{**v, "changes": t.get("changes", {})} for v, t in zip(versions.by_version(procs, tl), tl)],
        "version": tl[-1]["version"] if tl else 1,
        "retired": {k: v.get("status") for k, v in (st.get("retired_jobs") or {}).items()},
        "jobs": jobs, "by_kind": _group(procs, "kind"), "by_model": _group(procs, "model")}
