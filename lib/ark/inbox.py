"""Commands waiting for ark-loop, the only writer of a run's state: `ark answer`, `ark steer`, `ark attach`, `ark stop`
post here and the loop applies them on its next cycle.

It is outside the run folders on purpose. A worker can write there, and an item here speaks for Marco: a forged
`raise` would lift a spending cap. desk-guard denies runner processes this folder and the commands refuse them."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from . import paths

MAX_ITEM = 1 << 20  # bytes


def folder(repo: str, slug: str) -> Path:
    return paths.ark_home() / "inbox" / repo / slug


def post(repo: str, slug: str, kind: str, **data) -> Path:
    d = folder(repo, slug)
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".post.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({**data, "kind": kind}, f, ensure_ascii=False)
        target = d / f"{time.time_ns()}-{kind}.json"
        os.replace(tmp, target)
        return target
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def pending(repo: str, slug: str) -> list[dict]:
    """Oldest first, each with `kind` and `_path`. Anything unreadable has kind `unreadable`: the loop clears it."""
    out = []
    for f in sorted(folder(repo, slug).glob("*.json")):
        try:
            item = json.loads(paths.read_regular(f, MAX_ITEM))
        except (OSError, ValueError, RecursionError):
            item = None
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
            item = {"kind": "unreadable"}
        out.append({**item, "_path": str(f)})
    return out


def done(item: dict) -> None:
    Path(item["_path"]).unlink(missing_ok=True)


def drop_all(repo: str, slug: str) -> None:
    for item in pending(repo, slug):
        done(item)
