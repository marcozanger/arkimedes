"""history/jobs.jsonl and history/plans.jsonl (spec §9a): one row per job and one per plan, for the next plans.

A run recorded again (a report made twice, a backfill) replaces its own rows, so the files never count a run
twice. Writers take history/.lock; every file is replaced whole."""
from __future__ import annotations

import fcntl
import json
import re

from . import gitops, paths, run

SHORTSTAT = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")


def history_dir():
    return paths.ark_home() / "history"


def _read(name: str) -> list[dict]:
    try:
        text = paths.read_regular(history_dir() / name).decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _write(name: str, rows: list[dict]) -> None:
    run._atomic(history_dir() / name, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def size(files) -> str:
    if files is None:
        return "?"
    return "S" if files <= 3 else "M" if files <= 10 else "L"


def diffstat(repo_dir, base, head) -> tuple[int | None, int | None]:
    """Files and lines a job changed, or (None, None) when its commits are not in the repo any more."""
    if not (repo_dir and base and head):
        return None, None
    try:
        out = gitops.git(repo_dir, "diff", "--shortstat", base, head, check=False, timeout=30)
    except Exception:
        return None, None
    m = SHORTSTAT.search(out or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2) or 0) + int(m.group(3) or 0)


def failure(reason: str) -> str | None:
    """A reason cut to its kind: `job gate red (exit 1): …` -> `job gate red`."""
    head = re.split(r"[:(]", reason or "", maxsplit=1)[0].strip()
    return head[:80] or None


def job_rows(st: dict, s: dict, mjobs: dict, repo_dir) -> list[dict]:
    rows = []
    for jid, j in s["jobs"].items():
        sj, mj = st["jobs"].get(jid) or {}, mjobs.get(jid) or {}
        files, changed = diffstat(repo_dir, sj.get("base"), sj.get("head"))
        rows.append({
            "repo": st["repo"], "plan": st["slug"], "job": jid, "closed": s["ended"],
            "tasks": list(mj.get("tasks") or []), "area": len(mj.get("area") or []),
            "files": files, "lines": changed, "size": size(files),
            "tier_start": j["tiers"][0] if j["tiers"] else None, "tier_end": j["tiers"][-1] if j["tiers"] else None,
            "attempts": j["attempts"], "steers": j["steers"], "reviews": j["reviews"],
            "gate_fails": j["gate_fails"], "integrate_fails": j["integrate_fails"],
            "minutes": j["minutes"], "tokens": j["tokens"], "usd": j["usd"],
            "budget_minutes": j["budget_minutes"], "budget_usd": j["budget_usd"],
            "outcome": j["status"], "failure": failure(j["reason"]) if j["status"] == "blocked" else None,
            "estimated": j["estimated"]})
    return rows


def plan_row(s: dict) -> dict:
    keys = ("repo", "status", "started", "ended", "wall_minutes", "working_minutes", "waiting_minutes",
            "merged", "blocked", "integrate_fails", "usd", "tokens", "budget_usd", "budget_minutes", "estimated")
    return {"plan": s["slug"], "jobs": len(s["jobs"]), **{k: s[k] for k in keys}}


def record(st: dict, s: dict, mjobs: dict, repo_dir) -> None:
    d = history_dir()
    d.mkdir(parents=True, exist_ok=True)
    mine = lambda r: (r.get("repo"), r.get("plan")) == (st["repo"], st["slug"])
    with open(d / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _write("jobs.jsonl", [r for r in _read("jobs.jsonl") if not mine(r)] + job_rows(st, s, mjobs, repo_dir))
        _write("plans.jsonl", [r for r in _read("plans.jsonl") if not mine(r)] + [plan_row(s)])
