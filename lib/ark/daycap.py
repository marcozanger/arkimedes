"""Daily cap questions, one per account and day (spec: account-caps). Only ark-loop writes runs/daycap.json;
answers reach it through inbox/@account/caps/, which runner processes cannot write."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import caps, inbox, paths, run
from .questions import _positive, _text

INBOX = ("@account", "caps")  # `@` starts no repo name, so this folder never meets a run's


def _file() -> Path:
    return paths.runs_dir() / "daycap.json"


def _fresh() -> dict:
    return {"day": caps.today(), "seq": 0, "questions": [], "answers": [], "stopped": []}


def load() -> dict:
    """Today's file; another day's, or one that cannot be read, starts clean (open questions close on their own)."""
    try:
        d = json.loads(paths.read_regular(_file()))
    except (OSError, ValueError):
        return _fresh()
    if not isinstance(d, dict) or d.get("day") != caps.today():
        return _fresh()
    return {**_fresh(), **d}


def save(d: dict) -> None:
    target = _file()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".daycap.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def find(d: dict, qid) -> dict | None:
    return next((q for q in d["questions"] if q["id"] == qid), None)


def ask(d: dict, account: str, text: str) -> tuple[dict | None, bool]:
    """(the open question of this account, whether it is new); (None, False) when he said stop today."""
    if account in d["stopped"]:
        return None, False
    for q in d["questions"]:
        if q["account"] == account:
            return q, False
    d["seq"] += 1
    q = {"id": f"c{d['seq']}", "account": account, "text": _text(text), "asked": run.now()}
    d["questions"].append(q)
    return q, True


def drop_except(d: dict, capped: set) -> None:
    """A question whose account is no longer capped (its config was raised) needs no answer."""
    d["questions"] = [q for q in d["questions"] if q["account"] in capped]


def check_answer(text: str) -> str | None:
    words = text.strip().split()
    if words == ["stop"] or (len(words) == 2 and words[0] == "raise" and _positive(words[1])):
        return None
    return "answer `raise <usd>` to add to today's cap, or `stop` to wait until tomorrow"


def apply(d: dict, qid: str, text: str) -> str | None:
    """Apply one answer. Returns an event text, or None when the question is gone or the answer is bad."""
    q = find(d, qid)
    if q is None or check_answer(text):
        return None
    words = text.split()
    if words == ["stop"]:
        d["stopped"].append(q["account"])
        said = f"{q['account']}: waits until tomorrow"
    else:
        caps.record_raise(q["account"], float(words[1]))
        said = f"{q['account']}: today's cap raised by ${float(words[1]):.2f}"
    d["questions"].remove(q)
    d["answers"].append({**q, "answer": text.strip(), "at": run.now()})
    return said


def post(qid: str, text: str) -> None:
    inbox.post(*INBOX, "answer", id=qid, text=text)


def pending() -> list[dict]:
    return inbox.pending(*INBOX)
