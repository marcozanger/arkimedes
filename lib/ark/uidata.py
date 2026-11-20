"""History and Euklides data for the control center (read-only)."""
from __future__ import annotations

from . import claude, run
from . import euklides as _euk
from . import history as _hist

LAST_DECISIONS = 30
HISTORY_KEYS = ("status", "jobs", "started", "ended", "wall_minutes", "budget_minutes", "usd", "budget_usd")


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def history() -> list[dict]:
    rows = [{"repo": str(r.get("repo") or ""), "slug": str(r.get("plan") or ""), **{k: r.get(k) for k in HISTORY_KEYS}}
            for r in _safe(lambda: _hist._read("plans.jsonl"), [])]
    return sorted(rows, key=lambda r: str(r["ended"] or ""), reverse=True)


def _amendments(decisions: list[dict]) -> dict:
    """Decision id -> the amendments it made, read from each run's state once."""
    out = {}
    for repo, slug in {(d.get("repo"), d.get("slug")) for d in decisions}:
        st = _safe(lambda: run.load(repo, slug), None) if repo and slug else None
        for a in (st.get("amendments") or []) if isinstance(st, dict) else []:
            if a.get("decision"):
                out.setdefault(a["decision"], []).append(a)
    return out


def euklides() -> dict:
    decisions = _safe(_euk.decisions, [])
    reviews = {}
    for r in _safe(_euk.reviews, []):  # the last review of a decision wins
        reviews[r.get("id")] = {"verdict": r.get("verdict"), "comment": r.get("comment")}
    last = sorted(decisions, key=lambda d: str(d.get("at") or ""), reverse=True)[:LAST_DECISIONS]
    amended = _amendments(last)
    return {"model": getattr(claude, "EUKLIDES_MODEL", None), "usd_per_call": getattr(claude, "EUKLIDES_USD", None),
            "minutes_per_call": getattr(claude, "EUKLIDES_MINUTES", None),
            "tools": getattr(claude, "EUKLIDES_TOOLS", None), "max_per_job": getattr(_euk, "MAX_PER_JOB", None),
            "lessons": _safe(_euk.lessons, None),
            "decisions": [{**d, "review": reviews.get(d.get("id")), "amendments": amended.get(d.get("id"), [])}
                          for d in last],
            "review_command": "ark review"}
