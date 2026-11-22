"""What only Marco can answer (spec §8). A question lives in its run's state, under `questions`, and moves to `answers`
when he answers it. questions.md is the same list for people, rewritten by the loop, never read back.

Kinds: worker (a job waits for it), cap_run (a run is over its budgets), plan_changed (a run is held)."""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

from . import paths, run
from .usage import clean

MAX_TEXT = 2000
STOP_JOB = ":stop"  # to a worker's question: no answer, the job ends blocked
OK_WORDS = ("ok", "yes", "s\u00ed", "si")  # to Euklides' ask: the runner carries out its `then`


def _text(text) -> str:
    return clean(str(text)).strip()[:MAX_TEXT]


MAX_LABEL = 60
MAX_DETAIL = 300


def option(label: str, detail: str, answer: str, confirm: bool = False) -> dict:
    """One button of a question: what it says, what the runner then does, and the answer text it posts."""
    return {"label": _text(label)[:MAX_LABEL], "detail": _text(detail)[:MAX_DETAIL], "answer": str(answer),
            "confirm": bool(confirm)}


def ask(st: dict, kind: str, text: str, job: str | None = None, critical: bool = False, quiet: bool = False,
        options: list[dict] | None = None) -> dict:
    """The open question of this kind and job, or a new one. An open one keeps its own options."""
    open_questions = st.setdefault("questions", [])
    for q in open_questions:
        if q["kind"] == kind and q["job"] == job:
            return q
    st["question_seq"] = st.get("question_seq", 0) + 1
    q = {"id": f"q{st['question_seq']}", "kind": kind, "job": job, "text": _text(text), "asked": run.now()}
    if options:
        q["options"] = [option(o.get("label", ""), o.get("detail", ""), o["answer"], o.get("confirm", False))
                        for o in options]
    open_questions.append(q)
    if quiet:
        run.event(st, f"question {q['id']}: {q['text'][:300]}")
    else:
        who = f" from {job}" if job else ""
        run.alert(st, f"question {q['id']}{who}: {q['text'][:300]} — `ark answer`", critical=critical)
    return q


def find(st: dict, qid) -> dict | None:
    return next((q for q in st.get("questions") or [] if q["id"] == qid), None)


def drop_kind(st: dict, kind: str, job: str | None = None) -> None:
    """Close questions nobody needs answered any more. They leave no answer behind."""
    if st.get("questions"):
        st["questions"] = [q for q in st["questions"] if not (q["kind"] == kind and (job is None or q["job"] == job))]


def _positive(word: str) -> bool:
    try:
        n = float(word)
    except ValueError:
        return False
    return math.isfinite(n) and n > 0


def options(q: dict) -> list[dict]:
    """The question's options of a usable shape; none for a question asked without them."""
    opts = q.get("options")
    if not isinstance(opts, list):
        return []
    return [o for o in opts if isinstance(o, dict) and isinstance(o.get("answer"), str)]


def number(text: str) -> int | None:
    """The option number an answer names, or None when it is not a bare number."""
    text = str(text).strip()
    return int(text) if text.isascii() and text.isdigit() and len(text) < 6 else None


def pick(q: dict, text: str) -> str:
    """The answer text of the option an answer names by number; any other text unchanged."""
    n, opts = number(text), options(q)
    if opts and n is not None and 1 <= n <= len(opts):
        return opts[n - 1]["answer"]
    return text


def check_answer(q: dict, text: str) -> str | None:
    """What is wrong with this answer, or None."""
    text = text.strip()
    if not text:
        return "the answer is empty"
    words = text.split()
    if q["kind"] == "cap_run" and not (words == ["stop"] or (len(words) == 2 and words[0] == "raise" and _positive(words[1]))):
        return "answer `raise <usd>` or `stop`"
    if q["kind"] == "worker" and q.get("refuse_ok") and text.lower() in OK_WORDS:
        return q["refuse_ok"]
    if q["kind"] == "plan_changed" and text != "stop":
        return "answer `stop` to end the run; to go on, restore the approved text or approve the new one"
    return None


def resolve(st: dict, qid: str, text: str) -> dict:
    q = find(st, qid)
    st["questions"].remove(q)
    answer = {"id": q["id"], "kind": q["kind"], "job": q["job"], "question": q["text"], "answer": text.strip(),
              "asked": q.get("asked"), "at": run.now()}
    chosen = next((o for o in options(q) if o["answer"].strip() == text.strip()), None)
    if chosen:
        answer["option"] = chosen.get("label", "")
    st.setdefault("answers", []).append(answer)
    return answer


def render(states: list[dict], cap_questions=()) -> str:
    lines = ["# Questions", "", "Answer with `ark answer`.", ""]
    found = False
    for q in cap_questions:  # an account at its daily cap (daycap.py): first, it holds every run of that account
        found = True
        lines += [f"## account {q['account']} · {q['id']}", "", q["text"], ""]
    for st in states:
        for q in st.get("questions") or []:
            found = True
            job = f" · job {q['job']}" if q["job"] else ""
            lines += [f"## {st['repo']}/{st['slug']} · {q['id']}{job}", "", f"Plan: {st['plan']}", "", q["text"], ""]
    if not found:
        lines.append("None open.")
    return clean("\n".join(lines)).rstrip() + "\n"  # the plan comes from the approval, not from a state _load checked


def save_file(states: list[dict], cap_questions=()) -> None:
    """Only when it changed. A new file put in place, so a link planted at the name is replaced, not followed."""
    target = paths.ark_home() / "questions.md"
    text = render(states, cap_questions)
    exists = os.path.lexists(target)
    if not exists and not cap_questions and not any(st.get("questions") for st in states):
        return
    if exists and not target.is_symlink():
        try:
            if paths.read_regular(target) == text.encode("utf-8"):
                return
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".questions.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
