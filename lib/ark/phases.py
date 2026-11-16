"""Where a job's minutes usually go: developing, testing, reviewing, other. Read-only and derived: it reads the
steps.jsonl of the repo's earlier runs (the same rows the report shows), so it needs no new data. It is a history,
not a budget: a plan's minutes are split by the shares past jobs of the repo had."""
from __future__ import annotations

import json
from pathlib import Path

from . import paths, steps

PHASE_OF = {"worker": "developing", "euklides": "developing", "gate": "testing", "integrate": "testing",
            "review": "reviewing", "judge": "other", "intervene": "other", "decisions": "other"}
ORDER = ("developing", "testing", "reviewing", "other")
MIN_MINUTES = 60  # less history than this in a repo: borrow every repo's


def _totals(files: list[Path]) -> dict[str, float]:
    t = dict.fromkeys(ORDER, 0.0)
    for f in files:
        try:
            lines = paths.read_regular(f, steps.RECEIPT_LIMIT * 16).decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ph, m = PHASE_OF.get(r.get("kind")), steps.num(r.get("minutes"))
            if ph and m is not None:
                t[ph] += m
    return t


def shares(repo: str) -> dict | None:
    """{"basis": "<repo>"|"all repos", "minutes": total, "share": {phase: 0..1}} from earlier runs, or None."""
    root = paths.runs_dir()
    mine = _totals(sorted((root / repo).glob("*/steps.jsonl")))
    basis, t = repo, mine
    if sum(mine.values()) < MIN_MINUTES:
        basis, t = "all repos", _totals(sorted(root.glob("*/*/steps.jsonl")))
    total = sum(t.values())
    if total < MIN_MINUTES:
        return None
    return {"basis": basis, "minutes": round(total), "share": {k: round(v / total, 3) for k, v in t.items()}}


def split(minutes: float, sh: dict | None) -> dict | None:
    """A budget of `minutes` cut by the shares: {phase: minutes}, or None when there is no history to go on."""
    if not sh or not minutes:
        return None
    return {k: round(minutes * v, 1) for k, v in sh["share"].items()}
