"""Plan times for the control center (spec: Time calculations). Budgets give the estimate; real times
from state.json and budgets for the rest give the forecast. Pure: no I/O."""
from __future__ import annotations

from datetime import datetime, timedelta

TERMINAL = {"merged", "blocked"}


def parse(s) -> datetime | None:
    """An aware datetime, or None: a naive or unreadable time cannot be compared with now."""
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else None


def _order(jobs: list[dict]) -> list[dict]:
    """Every job after its `after` jobs, otherwise in manifest order. A cycle (validation forbids it) keeps the rest as is."""
    ids = {j["id"] for j in jobs}
    done, out, rest = set(), [], list(jobs)
    while rest:
        ready = [j for j in rest if all(a in done or a not in ids for a in j.get("after", []))]
        if not ready:
            return out + rest
        for j in ready:
            out.append(j)
            done.add(j["id"])
            rest.remove(j)
    return out


def _minutes(d: timedelta) -> int:
    return round(d.total_seconds() / 60)


def compute(jobs: list[dict], status: str, now: datetime) -> dict:
    """jobs: manifest order, each {id, after, minutes, status, started, finished}. Datetimes out, minutes as ints."""
    origin = min([t for t in (parse(j.get("started")) for j in jobs) if t] or [now])
    est: dict[str, tuple] = {}
    fc: dict[str, tuple] = {}
    for j in _order(jobs):
        jid, budget = j["id"], timedelta(minutes=j.get("minutes") or 0)
        start = max([est[a][1] for a in j.get("after", []) if a in est] or [origin])
        est[jid] = (start, start + budget)
        started, finished = parse(j.get("started")), parse(j.get("finished"))
        if j["status"] in TERMINAL and started and finished:
            fc[jid] = (started, finished)
        elif j["status"] == "merged":                 # a run from before the times were kept
            fc[jid] = est[jid]
        elif j["status"] == "blocked":
            fc[jid] = (started, finished or now) if started else (None, None)
        elif started:
            fc[jid] = (started, max(now, started + budget))
        else:
            s = max([now] + [fc[a][1] for a in j.get("after", []) if a in fc and fc[a][1]])
            fc[jid] = (s, s + budget)
    est_end = max((e for _, e in est.values()), default=None)
    fc_end = max((e for _, e in fc.values() if e), default=None)
    delay = _minutes(fc_end - est_end) if est_end and fc_end else None
    if status == "done":
        left = 0
    elif status in ("held", "blocked") or fc_end is None or any(j["status"] == "blocked" for j in jobs):
        left = None
    else:
        left = max(0, _minutes(fc_end - now))
    def job_left(j):
        """Minutes this job still needs, from the forecast: 0 when it is over, None when it never started or is stuck.
        `over` is a job past its budget: the forecast has no better guess than "now", so the UI says so instead of 0."""
        if j["status"] in TERMINAL:
            return {"left_min": 0 if j["status"] == "merged" else None, "over": False}
        end = fc[j["id"]][1]
        if end is None:
            return {"left_min": None, "over": False}
        started = parse(j.get("started"))
        over = bool(started and now > started + timedelta(minutes=j.get("minutes") or 0))
        return {"left_min": max(0, _minutes(end - now)), "over": over}

    byid = {j["id"]: j for j in jobs}
    return {
        "origin": origin, "estimated_end": est_end, "forecast_end": fc_end, "delay_min": delay, "left_min": left,
        "jobs": {jid: {"plan_start": est[jid][0], "plan_end": est[jid][1], "fc_start": fc[jid][0], "fc_end": fc[jid][1],
                       **job_left(byid[jid])} for jid in est},
    }
