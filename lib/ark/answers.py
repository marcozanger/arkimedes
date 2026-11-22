"""Posting Marco's answer to a run's open question: behind `ark answer` and `ark ui`. ark-loop applies it."""
from __future__ import annotations

import os

from . import inbox, questions, run


class AnswerError(Exception):
    def __init__(self, kind: str, message: str, answer: dict | None = None):
        super().__init__(message)
        self.kind, self.answer = kind, answer   # kind: role · no-run · closed · posted · unknown · rejected


def post(repo: str, slug: str, qid: str, text: str) -> dict:
    if "ARK_ROLE" in os.environ:
        raise AnswerError("role", "this command does not run inside a runner process.")
    st = run.load(repo, slug)
    if not st or st.get("status") in run.FINISHED:
        raise AnswerError("no-run", f"no live run {repo}/{slug}")
    q = questions.find(st, qid)
    if q is None:
        done = next((a for a in st.get("answers") or [] if a.get("id") == qid), None)
        if done:
            raise AnswerError("closed", f"{qid} was already answered", done)
        raise AnswerError("unknown", f"{repo}/{slug} has no open question {qid}")
    if any(i["kind"] == "answer" and i.get("id") == qid for i in inbox.pending(repo, slug)):
        raise AnswerError("posted", f"{qid} already has an answer waiting for ark-loop")
    n, opts = questions.number(text), questions.options(q)
    if opts and n is not None:  # an option by its number
        if not 1 <= n <= len(opts):
            raise AnswerError("rejected", f"answer rejected: no option {n}")
        text = questions.pick(q, text)
    problem = questions.check_answer(q, text)
    if problem:
        raise AnswerError("rejected", f"answer rejected: {problem}")
    inbox.post(repo, slug, "answer", id=q["id"], text=text)
    return {"id": q["id"], "text": text}
