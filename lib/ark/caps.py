"""Spending caps (spec §8). `runs/spend.jsonl` is one line per paid process; only the daily cap reads it.
A ledger that cannot be read counts as over the cap: an unknown spend must not start new spending."""
from __future__ import annotations

import json
import math
import os
from datetime import date

from . import accounts, paths, run


def today() -> str:
    return date.today().isoformat()


def _ledger():
    return paths.runs_dir() / "spend.jsonl"


def _raises():
    return paths.runs_dir() / "raises.jsonl"


def _repo_of(run_key) -> str:
    return str(run_key).rpartition("/")[0]


def _append(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(data) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def record(run_key: str, name: str, usd: float) -> None:
    if not usd > 0:
        return
    _append(_ledger(), {"day": today(), "at": run.now(), "run": run_key, "name": name, "usd": usd,
                        "account": accounts.account_of(_repo_of(run_key))})


def _money(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _today_lines(path):
    """Today's dict lines with a usable usd. Raises OSError when the file cannot be read."""
    try:
        text = paths.read_regular(path).decode("utf-8", "replace")
    except FileNotFoundError:
        return []
    out, day = [], today()
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("day") == day and _money(d.get("usd")):
            out.append(d)
    return out


def _account_of_line(d: dict) -> str:
    a = d.get("account")
    return a if isinstance(a, str) and a else accounts.account_of(_repo_of(d.get("run", "")))


def spent_today(account: str | None = None) -> float:
    """Today's spend of one account, or of all of them. An unreadable ledger is infinite."""
    try:
        lines = _today_lines(_ledger())
    except OSError:
        return float("inf")
    return sum(d["usd"] for d in lines if account is None or _account_of_line(d) == account)


def record_raise(account: str, usd: float) -> None:
    _append(_raises(), {"day": today(), "at": run.now(), "account": account, "usd": usd})


def raised_today(account: str) -> float:
    """An unreadable raises ledger counts as no raise: unknown money must not start new spending."""
    try:
        lines = _today_lines(_raises())
    except OSError:
        return 0.0
    return sum(d["usd"] for d in lines if d.get("account") == account)


def day_limit(limits: dict, account: str = accounts.PERSONAL) -> float | None:
    if account != accounts.PERSONAL:
        return accounts.rule_of_account(account).get("usd_per_day")  # validated by accounts._rules
    limit = limits.get("usd_per_day")
    if limit is None:
        return None
    if not _money(limit) or limit <= 0:
        raise ValueError(f"limits.usd_per_day must be a positive number, not {limit!r}")
    return limit


def check_gates(limits: dict) -> None:
    """gates_total: an integer of 1 or more (a missing value was filled with 2 by paths.load_workspace)."""
    v = limits.get("gates_total", 2)
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError(f"limits.gates_total must be an integer of 1 or more, not {v!r}")


def check_limits(limits: dict) -> None:
    """Every cap valid, or raise: the personal limit (ValueError) and every rule (AccountError)."""
    day_limit(limits)
    check_gates(limits)
    accounts._rules()


def capped(account: str, limits: dict) -> bool:
    limit = day_limit(limits, account)
    return limit is not None and spent_today(account) >= limit + raised_today(account)


def run_spent(st: dict) -> float:
    return sum(j["usd"] for j in st["jobs"].values()) + st.get("other_usd", 0)


def run_budget(mjobs: dict) -> float:
    return sum(mj["budget"]["usd"] for mj in mjobs.values())
