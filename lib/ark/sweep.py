"""The sweep: what finished and abandoned runs leave behind (worktrees, ark/* branches here and on origin, their
open PRs, empty folders), what of it must stay, and clearing the rest.

A run is sweepable when it is aborted, done with every job merged, or abandoned: closed blocked (or done with a
job not merged) for `sweep.abandon_days` without a retry. Anything a live run or a run still inside that window
could need is never touched, and neither is anything with work that exists nowhere else."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import paths

DEFAULTS = {"enabled": True, "abandon_days": 14}


def settings() -> dict:
    """The workspace's `sweep` block over DEFAULTS; a bad value or an unknown key is a ValueError naming it."""
    block = paths.load_workspace().get("sweep")
    if block is None:
        block = {}
    if not isinstance(block, dict):
        raise ValueError("sweep must be an object")
    for k in block:
        if k not in DEFAULTS:
            raise ValueError(f"sweep.{k} is not a setting (known: {', '.join(DEFAULTS)})")
    out = {**DEFAULTS, **block}
    if not isinstance(out["enabled"], bool):
        raise ValueError("sweep.enabled must be true or false")
    days = out["abandon_days"]
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("sweep.abandon_days must be a whole number of days, at least 1")
    return out


def _when(text) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        t = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def closed_at(st: dict) -> datetime | None:
    """When the run closed: `closed_at` when the state has it, else the time of its last event."""
    if st.get("closed_at"):
        return _when(st["closed_at"])
    events = st.get("events") or []
    if events and isinstance(events[-1], str) and " · " in events[-1]:
        return _when(events[-1].split(" · ", 1)[0])
    return None


def classify(st: dict, now: datetime) -> tuple[str, datetime | None]:
    """(kind, since): live, aborted, done, abandoned (since the day it became so) or window (until that day).
    A closed run with no close time at all stays in the window for good: there is no date to count from."""
    status = st.get("status")
    if status in ("running", "waiting") or st.get("closing") != "done":
        return "live", None
    closed = closed_at(st)
    if status == "aborted":
        return "aborted", closed
    jobs = st.get("jobs") or {}
    if status == "done" and all(j.get("status") == "merged" for j in jobs.values()):
        return "done", closed
    if closed is None:
        return "window", None
    until = closed + timedelta(days=settings()["abandon_days"])
    return ("abandoned" if now >= until else "window"), until


def abandoned(st: dict, now: datetime) -> datetime | None:
    kind, since = classify(st, now)
    return since if kind == "abandoned" else None
