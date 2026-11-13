"""Euklides: the runner's decision maker.

The loop calls it when a job attempt failed, a worker asked a question, or a job hit a block a new
attempt can fix. The runner, not the model, decides which actions are open (`allowed`); the model
only picks one of them and classifies the reach of an answer (`undo`). Every decision is journaled
under history/euklides/ for Marco's review (`ark review`)."""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path

from . import claude, history, paths, run

DEFAULTS = {
    "model": "opus",
    "usd_per_call": 5,
    "minutes_per_call": 30,
    # it repairs the job's branch itself; the push layer and merge-guard keep pushes and PRs the runner's
    "tools": ["Read", "Grep", "Glob", "Bash", "Edit", "Write"],
    "denied_tools": ["WebFetch", "WebSearch", "Task", "Agent", "NotebookEdit"],
    "budget_margin": 0.10,  # read, not used yet
    "decisions_per_job": 4,  # decisions without Marco per job and run; after that Euklides can only ask
}
MAX_FIX_BASE = 3  # base fixes per run without Marco
TRIGGERS = ("failure", "question", "blocked")
KEYS = {"action", "note", "undo", "why", "proposal", "files"}
NOTE_CHARS, WHY_CHARS, PROPOSAL_CHARS = 1000, 300, 1000
SITUATION_CHARS = 600
LESSONS_CHARS = 8000  # a hand-edited file that grew too long is cut, never fails the call


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _tools(v) -> bool:
    return isinstance(v, list) and all(isinstance(t, str) and t.strip() for t in v)


def settings() -> dict:
    """DEFAULTS overridden by the `euklides` block of workspace.json. Raises ValueError naming euklides.<key>."""
    block = paths.load_workspace().get("euklides")
    if block is None:
        block = {}
    if not isinstance(block, dict):
        raise ValueError("euklides must be an object")
    for k in block:
        if k not in DEFAULTS:
            raise ValueError(f"euklides.{k} is not a setting (known: {', '.join(DEFAULTS)})")
    out = {**{k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}, **block}
    m = out["model"]
    if not (isinstance(m, str) and (m in ("haiku", "sonnet", "opus") or m.startswith("claude-"))):
        raise ValueError("euklides.model must be haiku, sonnet, opus or a string starting with claude-")
    for k in ("usd_per_call", "minutes_per_call"):
        if not _num(out[k]) or out[k] <= 0:
            raise ValueError(f"euklides.{k} must be a positive number")
    for k in ("tools", "denied_tools"):
        if not _tools(out[k]):
            raise ValueError(f"euklides.{k} must be a list of non-empty strings")
    both = sorted(set(out["tools"]) & set(out["denied_tools"]))
    if both:
        raise ValueError(f"euklides.tools and euklides.denied_tools both list {', '.join(both)}")
    if not _num(out["budget_margin"]) or not 0 <= out["budget_margin"] <= 1:
        raise ValueError("euklides.budget_margin must be a number between 0 and 1")
    d = out["decisions_per_job"]
    if not isinstance(d, int) or isinstance(d, bool) or d < 0:
        raise ValueError("euklides.decisions_per_job must be an integer of 0 or more")
    return out


def allowed(trigger: str, j: dict, budget_usd: float, kind: str | None = None, base_red: bool = False,
            fixes_used: int = 0) -> list[str]:
    """The actions open now, `ask` always last. Spending past the budget, changing the plan and
    blocking are never actions: they can only be proposed to Marco. `widen` opens only after an area
    block, `fix_base` only after an integration gate red on the base too, at most MAX_FIX_BASE a run.
    `fixed` (Euklides repaired the branch himself) is open while decisions remain."""
    if j.get("euklides_count", 0) >= settings()["decisions_per_job"]:
        return ["ask"]
    under = j["usd"] < budget_usd
    out = []
    if trigger == "failure" and under:
        out.append("retry")
        if j["tier"] in claude.NEXT_TIER:
            out.append("escalate")
    elif trigger == "question":
        out.append("answer")  # an answer costs no attempt by itself: the worker goes on in its session
    elif trigger == "blocked" and under:
        out.append("unblock")
        if kind == "area":
            out.append("widen")
    if trigger == "blocked" and kind == "integrate" and base_red and fixes_used < MAX_FIX_BASE:
        out.append("fix_base")  # the base's fix is not paid from the job's budget
    return out + ["fixed", "ask"]  # fixed: Euklides repaired the branch himself; the runner checks it


def _files(v) -> list[str] | None:
    """1 to 3 exact relative paths (no glob, no `..`, not absolute), or None."""
    if not isinstance(v, list) or not 1 <= len(v) <= 3:
        return None
    out = []
    for f in v:
        if not isinstance(f, str) or not f.strip():
            return None
        f = f.strip()
        if f.startswith("/") or ".." in f.split("/") or any(ch in f for ch in "*?[]{}\0"):
            return None
        out.append(f)
    return out


def _text(v, limit: int) -> str | None:
    """The stripped string, cut to `limit`; None when `v` is not a string."""
    return v.strip()[:limit] if isinstance(v, str) else None


def parse(d, allowed_now: list[str]) -> dict | None:
    """The decision in an answer, or None when it is not usable now (the loop then asks Marco).

    An `answer` whose mistake would reach the plan (`undo: plan`) comes back as an `ask` whose
    proposal is that answer, so Marco can take it with `ok`."""
    if not isinstance(d, dict) or not {"action", "note"} <= set(d) or not set(d) <= KEYS:
        return None
    action, note = d["action"], _text(d["note"], NOTE_CHARS)
    if not isinstance(action, str) or action not in allowed_now or not note:
        return None
    why, proposal = d.get("why", ""), d.get("proposal")
    if why is None:
        why = ""
    if not isinstance(why, str) or (proposal is not None and not isinstance(proposal, str)):
        return None
    why, proposal = why.strip()[:WHY_CHARS], (_text(proposal, PROPOSAL_CHARS) or None)
    undo = d.get("undo") if action == "answer" else None
    if action == "answer" and undo not in ("job", "plan"):
        return None
    if action == "ask" and not proposal:
        return None
    files = _files(d.get("files")) if action == "widen" else None
    if action == "widen" and files is None:
        return None
    if action == "answer" and undo == "plan":
        return {"action": "ask", "note": f"Euklides would answer this, but a wrong answer would reach beyond "
                                         f"this job: {why or 'no reason given'}",
                "undo": "plan", "why": why, "proposal": note}
    out = {"action": action, "note": note, "undo": undo, "why": why, "proposal": proposal}
    return {**out, "files": files} if files else out  # only a widen carries files


def folder() -> Path:
    return history.history_dir() / "euklides"


def lessons_file() -> Path:
    return folder() / "lessons.md"


@contextmanager
def _locked():
    folder().mkdir(parents=True, exist_ok=True)
    with open(folder() / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def decisions() -> list[dict]:
    return history._read("euklides/decisions.jsonl")


def reviews() -> list[dict]:
    return history._read("euklides/reviews.jsonl")


def _append(name: str, row: dict) -> None:
    with open(folder() / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def record(st: dict, job: str, ctx: dict, d: dict, *, valid: bool, usd: float) -> str:
    """Append one decision and return its id `<repo>/<slug>/<job>/<n>`, n counting every decision this
    job ever had in the journal (a slug planned twice keeps counting). Raises OSError."""
    with _locked():
        prefix = f"{st['repo']}/{st['slug']}/{job}/"
        n = 1 + sum(1 for r in decisions() if str(r.get("id", "")).startswith(prefix))
        row = {"id": f"{prefix}{n}", "at": run.now(), "repo": st["repo"], "slug": st["slug"], "job": job,
               "trigger": ctx["trigger"], "situation": str(ctx.get("situation") or "")[:SITUATION_CHARS],
               "action": d["action"], "note": d["note"], "undo": d["undo"], "why": d["why"],
               "proposal": d["proposal"], "valid": valid, "usd": usd, "marco": None}
        if d.get("files"):
            row["files"] = d["files"]
        _append("decisions.jsonl", row)
        return row["id"]


def set_marco(decision_id: str, answer: str) -> None:
    """Marco's answer to the question a decision asked, on that decision's line. Raises OSError."""
    with _locked():
        rows = decisions()
        if not any(r.get("id") == decision_id for r in rows):
            return
        history._write("euklides/decisions.jsonl",
                       [{**r, "marco": answer} if r.get("id") == decision_id else r for r in rows])


def add_review(decision_id: str, verdict: str, comment: str) -> None:
    with _locked():
        _append("reviews.jsonl", {"id": decision_id, "at": run.now(), "verdict": verdict, "comment": comment})


def lessons() -> str:
    try:
        return lessons_file().read_text(encoding="utf-8", errors="replace").strip()[:LESSONS_CHARS]
    except OSError:
        return ""
