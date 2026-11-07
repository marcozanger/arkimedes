"""history/insights.md: what history says about planning, in 30 lines at most (spec §9a). The desk reads it
before writing a manifest: how to split, which tier to start at, what budgets turned out to be."""
from __future__ import annotations

from collections import Counter
from statistics import median

from . import history, run

MAX_LINES = 30


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _ratio(rows, key, budget) -> str:
    rs = [r[key] / r[budget] for r in rows if _num(r.get(key)) is not None and _num(r.get(budget))]
    return f"{median(rs):.2f}" if rs else "–"


def _repo(repo: str, rows: list[dict]) -> list[str]:
    out = [f"## {repo} · {len(rows)} jobs"]
    tiers = sorted({r.get("tier_start") or "?" for r in rows})
    merged = [f"{t} {sum(r.get('outcome') == 'merged' for r in rows if (r.get('tier_start') or '?') == t)}/"
              f"{sum((r.get('tier_start') or '?') == t for r in rows)}" for t in tiers]
    out.append("- merged by starting tier: " + " · ".join(merged))
    sizes = []
    for size in ("S", "M", "L", "?"):
        sr = [r for r in rows if r.get("size") == size]
        usd = [r["usd"] for r in sr if _num(r.get("usd")) is not None]
        mins = [r["minutes"] for r in sr if _num(r.get("minutes")) is not None]
        if sr:
            sizes.append(f"{size} ${median(usd) if usd else 0:.2f} · {round(median(mins)) if mins else 0} min "
                         f"(n={len(sr)})")
    out.append("- median by size: " + " · ".join(sizes))
    out.append(f"- actual / budget (median): USD {_ratio(rows, 'usd', 'budget_usd')} · "
               f"time {_ratio(rows, 'minutes', 'budget_minutes')}")
    fails = Counter(r["failure"] for r in rows if r.get("failure")).most_common(3)
    if fails:
        out.append("- failures: " + " · ".join(f"{f} ×{n}" for f, n in fails))
    return out


def render(rows: list[dict]) -> str:
    lines = ["# Insights", "", "From history/jobs.jsonl, rewritten by `ark stats`. Sizes: files changed S ≤3, M ≤10, L.", ""]
    if not rows:
        return "\n".join(lines + ["No jobs in history yet."]) + "\n"
    by_repo: dict = {}
    for r in rows:
        by_repo.setdefault(str(r.get("repo")), []).append(r)
    for repo, rs in sorted(by_repo.items(), key=lambda kv: -len(kv[1])):
        lines += _repo(repo, rs) + [""]
    lines = lines[:-1]
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES - 1] + ["… more repos in history/jobs.jsonl"]
    return "\n".join(lines) + "\n"


def write():
    path = history.history_dir() / "insights.md"
    run._atomic(path, render(history._read("jobs.jsonl")))
    return path
