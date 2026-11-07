"""Spending caps (spec §8). `runs/spend.jsonl` is one line per paid process; only the daily cap reads it.
A ledger that cannot be read counts as over the cap: an unknown spend must not start new spending."""
from __future__ import annotations

import json
import math
import os
from datetime import date

from . import paths, run


def today() -> str:
    return date.today().isoformat()


def _ledger():
    return paths.runs_dir() / "spend.jsonl"


def record(run_key: str, name: str, usd: float) -> None:
    if not usd > 0:
        return
    line = json.dumps({"day": today(), "at": run.now(), "run": run_key, "name": name, "usd": usd}) + "\n"
    _ledger().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_ledger(), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def spent_today() -> float:
    try:
        text = paths.read_regular(_ledger()).decode("utf-8", "replace")
    except FileNotFoundError:
        return 0.0
    except OSError:
        return float("inf")
    day, total = today(), 0.0
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("day") == day and _money(d.get("usd")):
            total += d["usd"]
    return total


def _money(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def day_limit(limits: dict) -> float | None:
    limit = limits.get("usd_per_day")
    if limit is None:
        return None
    if not _money(limit) or limit <= 0:
        raise ValueError(f"limits.usd_per_day must be a positive number, not {limit!r}")
    return limit


def day_capped(limits: dict) -> bool:
    limit = day_limit(limits)
    return limit is not None and spent_today() >= limit


def run_spent(st: dict) -> float:
    return sum(j["usd"] for j in st["jobs"].values()) + st.get("other_usd", 0)


def run_budget(mjobs: dict) -> float:
    return sum(mj["budget"]["usd"] for mj in mjobs.values())
