"""Where alerts go besides the terminal: runs/alerts.jsonl, all of them, and the notifier, the critical ones (§8).

The loop has no model, so the notifier is a command from workspace.json: an argv, with the message appended as its
last argument. Nothing here may stop a cycle, so every failure comes back as text."""
from __future__ import annotations

import json
import re
import subprocess

from . import paths, run

MAX_PER_CYCLE = 5   # a cycle waits for the notifier: at SEND_TIMEOUT each, a burst must not stall the runner
SEND_TIMEOUT = 10   # seconds


NO_ORIGIN = {"repo": "", "slug": "", "job": ""}


def origin(st: dict, line: str) -> dict:
    """The run and job of a line run.alert wrote. The job is the text's first word when it names one of
    the run's jobs; alerts about the whole run name none."""
    prefix = f"{st['repo']}/{st['slug']}: "
    text = line[len(prefix):] if line.startswith(prefix) else line
    first = re.split(r"[\s:]", text, maxsplit=1)[0]
    return {"repo": st["repo"], "slug": st["slug"], "job": first if first in st.get("jobs", {}) else ""}


def record(alerts: list[str], critical: list[str], origins: dict | None = None) -> None:
    if not alerts:
        return
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    urgent, at, origins = set(critical), run.now(), origins or {}
    text = "".join(json.dumps({"at": at, "text": a, "critical": a in urgent, **origins.get(a, NO_ORIGIN)},
                              ensure_ascii=False) + "\n" for a in alerts)
    with open(paths.runs_dir() / "alerts.jsonl", "a", encoding="utf-8") as f:
        f.write(text)


def _messages(critical: list[str]) -> list[str]:
    if len(critical) <= MAX_PER_CYCLE:
        return list(critical)
    keep = MAX_PER_CYCLE - 1
    return critical[:keep] + [f"{len(critical) - keep} alerts more: see `ark ps` and runs/alerts.jsonl"]


def send(command: list[str], text: str) -> None:
    subprocess.run([*command, text], check=True, timeout=SEND_TIMEOUT, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def deliver(alerts: list[str], critical: list[str], origins: dict | None = None) -> str | None:
    """Record every alert and send the critical ones. What went wrong, or None."""
    problem = None
    try:
        record(alerts, critical, origins)
    except OSError as e:
        problem = f"cannot write alerts.jsonl: {e}"
    if not critical:
        return problem
    command = paths.load_workspace().get("notify")
    if not isinstance(command, list) or not all(isinstance(x, str) and x for x in command):
        return f"workspace.json: notify must be a list of strings, not {command!r}"
    if not command:
        return problem
    for text in _messages(critical):
        try:
            send(command, text)
        except (OSError, subprocess.SubprocessError) as e:
            return f"the notifier failed: {e}"
    return problem
