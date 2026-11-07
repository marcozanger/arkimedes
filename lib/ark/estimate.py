"""A plan's budget and its end-to-end time, from the Run manifest alone. No model, no I/O.

End-to-end is a simulation of the runner: a job starts as soon as every job in its `after` is done,
at most `writers` at once, and takes its budget minutes; ties go in manifest order. The review page
and the run report both use it, so they talk about the same number."""
from __future__ import annotations


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _minutes(job: dict):
    return _num(((job or {}).get("budget") or {}).get("minutes"))


def end_to_end(jobs: list[dict], writers: int):
    """Minutes from the first start to the last finish; None when it cannot be known."""
    if not jobs or any(_minutes(j) is None for j in jobs):
        return None
    pending = list(jobs)
    running: list[tuple[float, str]] = []
    done: set[str] = set()
    t = 0.0
    while pending or running:
        for job in list(pending):
            if len(running) >= max(1, writers):
                break
            if all(a in done for a in (job.get("after") or [])):
                running.append((t + _minutes(job), str(job["id"])))
                pending.remove(job)
        if not running:  # what is left waits on a job that never runs
            return None
        running.sort()
        t, jid = running.pop(0)
        done.add(jid)
    return t


def estimate(manifest: dict, writers: int) -> dict:
    jobs = list((manifest or {}).get("jobs") or [])
    rows = [{"id": str(j.get("id")), "tier": j.get("tier"), "after": list(j.get("after") or []),
             "minutes": _minutes(j), "usd": _num(((j.get("budget") or {}).get("usd")))} for j in jobs]
    mins = [r["minutes"] for r in rows]
    usds = [r["usd"] for r in rows]
    return {"jobs": rows, "writers": writers,
            "total_minutes": sum(mins) if rows and None not in mins else None,
            "total_usd": sum(usds) if rows and None not in usds else None,
            "end_to_end_minutes": end_to_end(jobs, writers)}
