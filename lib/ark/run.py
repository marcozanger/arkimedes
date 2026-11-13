"""Run state of one plan.

runs/<repo>/<slug>/state.json is the machine state (only ark-loop writes it);
runs/<repo>/<slug>.md is rendered from it for people and for `ark status` (its `Status:` line).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import paths

MAX_EVENTS = 200
TERMINAL = {"merged", "blocked"}
NOT_STARTED = {"pending", "needs_worker"}
FINISHED = ("done", "blocked", "aborted")  # a plan status the loop never steps: blocked may be reopened by `ark retry`


def area(st: dict, job: str, mj: dict) -> list[str]:
    """The job's real area: the plan's globs plus the literal files Euklides granted it in this run."""
    return [*mj["area"], *(st.get("grants") or {}).get(job, [])]


def stamp_times(st: dict) -> None:
    """`started` the first pass a job is past NOT_STARTED, `finished` the pass it reached TERMINAL.
    A job that leaves blocked loses `finished` and keeps `started`. The control center reads both.
    A run that was already finished when it first met this code has no real times to stamp: it is marked and left
    alone, since "now" would put every one of its jobs at the moment of the upgrade."""
    if not st.get("times_kept"):
        st["times_kept"] = True
        if st.get("closing") == "done" or st.get("status") in FINISHED:
            return
    t = now()
    for j in st["jobs"].values():
        if j["status"] not in NOT_STARTED and not j.get("started"):
            j["started"] = t
        if j["status"] in TERMINAL:
            if not j.get("finished"):
                j["finished"] = t
        else:
            j.pop("finished", None)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_state(approval: dict) -> dict:
    return {
        "repo": approval["repo"], "slug": approval["slug"], "plan": approval["plan"],
        "plan_sha": approval["sha256"], "approved_at": approval["approved_at"],
        "status": "waiting", "reason": "", "default_branch": None, "base_sha": None,
        "started": now(), "updated": now(), "closing": None, "final_pr": None,
        "events": [], "alerts": [], "jobs": {}, "times_kept": True,
    }


def new_job(mj: dict) -> dict:
    return {
        "status": "pending", "tier": mj["tier"], "attempts": 0, "tier_attempts": 0,
        "session": None, "review_rounds": 0, "integrate_fails": 0, "base": None, "head": None,
        "pr": None, "action": None, "failure": None, "setup_done": False, "reason": "",
        "tier_path": [mj["tier"]], "heavy": bool(mj.get("heavy", False)), "note": None,
        "resume_msg": None, "review_errors": 0, "usd": 0.0,
    }


def state_file(repo: str, slug: str) -> Path:
    return paths.run_dir(repo, slug) / "state.json"


class StateCorrupt(Exception):
    """state.json exists but is unreadable. Never treated as "no run yet"."""


def load(repo: str, slug: str) -> dict | None:
    f = state_file(repo, slug)
    if not os.path.lexists(f):
        return None
    try:
        st = json.loads(paths.read_regular(f).decode("utf-8"))
    except (OSError, ValueError) as e:  # a FIFO or a link in its place is damage too, never "no run yet"
        raise StateCorrupt(f"corrupt run state {f}: {e}") from e
    if not isinstance(st, dict):
        raise StateCorrupt(f"corrupt run state {f}: not a JSON object")
    return st


def event(st: dict, text: str) -> None:
    st["events"].append(f"{now()} · {text}")
    del st["events"][:-MAX_EVENTS]


def alert(st: dict, text: str, critical: bool = False) -> None:
    """Something Marco must hear. A critical one also reaches the notifier (spec §8), as alerts do the terminal."""
    line = f"{st['repo']}/{st['slug']}: {text}"
    st["alerts"].append(line)
    if critical:
        st.setdefault("critical", []).append(line)
    event(st, "ALERT " + text)


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")  # O_EXCL, O_NOFOLLOW: never a planted name
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def save(st: dict) -> None:
    st["updated"] = now()
    _atomic(state_file(st["repo"], st["slug"]), json.dumps(st, indent=2) + "\n")
    _atomic(paths.run_file(st["repo"], st["slug"]), render(st))


def render(st: dict) -> str:
    lines = [
        f"# Run {st['repo']}/{st['slug']}",
        "",
        f"Status: {st['status']}",
        f"Plan: {st['plan']} · sha256 {st['plan_sha'][:12]}",
    ]
    if st.get("base_sha"):
        lines.append(f"Integration branch: ark/{st['slug']} (from {st['default_branch']} @ {st['base_sha'][:8]})")
    lines.append(f"Started: {st['started']} · Updated: {st['updated']}")
    if st.get("reason"):
        lines.append(f"Reason: {st['reason']}")
    if st.get("final_pr"):
        lines.append(f"Final PR: {st['final_pr']['url']}")
    if st["jobs"]:
        lines += ["", "| job | status | tier | attempts | PR | head | note |", "|---|---|---|---|---|---|---|"]
        for jid, j in st["jobs"].items():
            pr = j["pr"]["url"] if j.get("pr") else "-"
            head = (j.get("head") or "-")[:8]
            lines.append(f"| {jid} | {j['status']} | {j['tier']} | {j['attempts']} | {pr} | {head} | {j.get('reason') or ''} |")
    lines += ["", "## Events", ""] + [f"- {e}" for e in st["events"]]
    return "\n".join(lines) + "\n"
