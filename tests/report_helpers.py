"""Fake run folders for the report tests: receipts and logs in done/, a state, a plan's jobs."""
import json
from datetime import datetime, timezone

T0 = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc).timestamp()
REPO, SLUG = "demo", "notes-edit"


def at(minutes: float) -> float:
    return T0 + minutes * 60


def iso(minutes: float) -> str:
    return datetime.fromtimestamp(at(minutes), timezone.utc).isoformat(timespec="seconds")


def done_dir():
    from ark import paths
    d = paths.run_dir(REPO, SLUG) / "done"
    d.mkdir(parents=True, exist_ok=True)
    return d


def result_line(model="claude-sonnet-5-5", inp=10, out=200, cache_read=3000, cache_write=400, usd=0.25,
                text="STATUS: DONE"):
    return json.dumps({"type": "result", "result": text, "total_cost_usd": usd, "session_id": "s",
                       "usage": {"input_tokens": 1, "output_tokens": 1},  # last turn only: must not be used
                       "modelUsage": {model: {"inputTokens": inp, "outputTokens": out,
                                              "cacheReadInputTokens": cache_read,
                                              "cacheCreationInputTokens": cache_write, "costUSD": usd}}}) + "\n"


def receipt(name, start_min, end_min, *, exit=0, reason="", result="STATUS: DONE", usd=0.25, meta=None, log=""):
    """A receipt the way proc.consume archives it, and its log (None: no log)."""
    d = done_dir()
    stamp = f"{name}-{int(at(end_min) * 1e9)}"
    (d / f"{stamp}.json").write_text(json.dumps({
        "name": name, "exit": exit, "reason": reason, "result": result, "usd": usd, "session": "s",
        "is_error": exit != 0, "start": at(start_min), "end": at(end_min), "meta": meta or {}, "proc": {}}))
    if log is not None:
        (d / f"{stamp}.log").write_text(log)
    return d / f"{stamp}.log"


def job(status="merged", **over):
    j = {"status": status, "tier": "T1", "tier_path": ["T1"], "attempts": 1, "integrate_fails": 0, "steers": 0,
         "base": None, "head": None, "pr": {"url": "https://example.test/pr/1"}, "reason": "", "usd": 0.0}
    j.update(over)
    return j


def run_state(**over):
    st = {"repo": REPO, "slug": SLUG, "plan": "/nowhere/plan.md", "plan_sha": "x", "status": "done",
          "closing": "done", "started": iso(0), "updated": iso(60), "approved_at": iso(-10),
          "final_pr": {"url": "https://example.test/pr/9"}, "events": [], "alerts": [], "questions": [],
          "answers": [], "jobs": {"shell": job(), "ui": job()}}
    st.update(over)
    return st


def mjobs():
    return {"shell": {"id": "shell", "tasks": ["T1", "T2"], "area": ["lib/a/**", "tests/a/**"],
                      "budget": {"minutes": 30, "usd": 3}},
            "ui": {"id": "ui", "tasks": ["T3"], "area": ["ui/**"], "budget": {"minutes": 20, "usd": 2}}}
