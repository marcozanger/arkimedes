"""runs/loop.json: ark-loop says it is alive at every poll, so a stopped loop is not taken for a quiet one.

Only ark-loop writes it; `ark runs --json` reads it for the control center."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import paths, run

MIN_WINDOW = 60  # seconds: one slow pass (gh, a notifier) must not look like a dead loop


def file() -> Path:
    return paths.runs_dir() / "loop.json"


def write(poll_seconds: float) -> str | None:
    """Never raises: what went wrong, or None."""
    try:
        run._atomic(file(), json.dumps({"beat": run.now(), "at": time.time(), "poll_seconds": poll_seconds,
                                        "pid": os.getpid()}) + "\n")
    except OSError as e:
        return f"cannot write loop.json: {e}"
    return None


def read(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        b = json.loads(file().read_text(encoding="utf-8"))
        at, poll = float(b["at"]), float(b["poll_seconds"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"alive": False, "last_beat": None, "pid": None}
    return {"alive": now - at <= max(3 * poll, MIN_WINDOW), "last_beat": b.get("beat"), "pid": b.get("pid")}
