"""One row per step of a run (spec §9a): every process, every merge, every wait for Marco.

Derived, not appended live: the rows are rebuilt from what the runner already keeps (the receipts and logs in
done/, the merge events and the answers in state.json), so a run that ended before P4 gets the same rows and
the loop's hot path does not change. Workers can write in the run folder: only regular files are read, and
every string taken from them is cleaned and cut."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

from . import brief, claude, paths, run, usage

LOG_LIMIT = 64 << 20                       # bytes of one log read for its result line
RECEIPT_LIMIT = 1 << 20
KINDS = ("worker", "review", "gate", "integrate", "euklides", "judge", "intervene")  # judge: runs recorded before Euklides
SCRIPTS = ("gate", "integrate")
DECISIONS = "_decisions.tick"
TIER_OF = {m: t for t, m in claude.MODELS.items()}       # "sonnet" -> "T1"
MERGED = re.compile(r"^(\S+) · ([a-z0-9][a-z0-9-]*): merged into ark/")
RESUMED = re.compile(r"^(\S+) · ([a-z0-9][a-z0-9-]*): resumed after an API failure \(\d+/\d+\): (.*)$")
ASKED = re.compile(r"^(\S+) · (?:ALERT )?question (q\d+)\b")
_FIELDS = {"in": "inputTokens", "out": "outputTokens", "cache_read": "cacheReadInputTokens",
           "cache_write": "cacheCreationInputTokens"}


def num(v):
    """A finite number >= 0, or None. bool is not a number here."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return v


def iso(t) -> str | None:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds") if num(t) is not None else None


def ts(text) -> float | None:
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return None


def _text(v, limit: int) -> str:
    return usage.clean(str(v or "")).strip()[:limit]


def _result(log) -> dict | None:
    """The last {"type": "result"} line of a log, or None."""
    try:
        text = paths.read_regular(log, LOG_LIMIT).decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "result":
            return d
    return None


def _model_usage(res) -> tuple[str | None, dict | None]:
    """The model that cost most and the tokens of all of them. modelUsage covers the whole session; the result's
    own `usage` is only its last turn."""
    mu = res.get("modelUsage") if isinstance(res, dict) else None
    if not isinstance(mu, dict) or not mu:
        return None, None
    tokens, best, cost = dict.fromkeys(_FIELDS, 0), None, -1.0
    for model, u in mu.items():
        if not isinstance(u, dict):
            continue
        for k, src in _FIELDS.items():
            tokens[k] += int(num(u.get(src)) or 0)
        c = num(u.get("costUSD")) or 0
        if c > cost:
            best, cost = _text(model, 60), c
    return best, tokens


def _short(model: str) -> str:
    """claude-sonnet-5-5 -> sonnet; anything else as it is."""
    return next((name for name in TIER_OF if name in model), model)


def _outcome(kind: str, r: dict) -> str:
    if r.get("reason"):
        return "stopped"
    text, code = str(r.get("result") or ""), r.get("exit")
    if kind == "worker":
        return brief.worker_status(text)[0].lower() if code == 0 else "error"
    if kind == "gate":
        if code == 0 and r.get("outcome") == "green (reused)":   # the worker's green gate, reused by the runner
            return "green (reused)"
        return "green" if code == 0 else "red"
    d = brief.parse_json_line(text)
    if kind == "review":
        verdict = d.get("verdict") if d else None
        return verdict.lower() if verdict in ("CLEAN", "FIX") else "no verdict"
    if kind == "integrate":
        return "no result" if d is None else ("green" if d.get("ok") is True else "red")
    return "ok" if code == 0 and not r.get("is_error") else "error"


def _name(r: dict) -> tuple[str | None, str | None]:
    name = str(r.get("name") or "")
    if name == DECISIONS:
        return None, "decisions"
    job, _, kind = name.rpartition(".")
    return (job, kind) if job and kind in KINDS else (None, None)


def blank(kind: str, job, start, end) -> dict:
    s, e = num(start), num(end)
    return {"step": 0, "kind": kind, "job": job, "attempt": None, "tier": None, "model": None,
            "start": iso(s), "end": iso(e),
            "minutes": round((e - s) / 60, 2) if s is not None and e is not None and e >= s else None,
            "tokens": None, "tokens_total": None, "usd": None, "outcome": "", "reason": "", "estimated": []}


def _row(r: dict, log, attempts: dict) -> dict | None:
    job, kind = _name(r)
    if kind is None:
        return None
    row = blank(kind, job, r.get("start"), r.get("end"))
    meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
    attempts[(job, kind)] = attempts.get((job, kind), 0) + 1
    row["attempt"] = meta["attempt"] if type(meta.get("attempt")) is int else attempts[(job, kind)]
    row["outcome"], row["reason"] = _outcome(kind, r), _text(r.get("reason"), 300)
    if row["minutes"] is None:
        row["estimated"].append("time")
    if kind in SCRIPTS:
        row["model"] = "script"
        return row
    row["usd"] = num(r.get("usd"))
    if row["usd"] is None:
        row["estimated"].append("usd")
    model, tokens = _model_usage(_result(log))
    if tokens is None:
        row["tokens_total"] = usage.scan(log)["tokens"] or None   # regular files only, like read_regular
        row["estimated"].append("tokens")
    else:
        row["tokens"], row["tokens_total"] = tokens, tokens["in"] + tokens["out"] + tokens["cache_write"]
    tier = meta.get("tier") if meta.get("tier") in claude.MODELS else None
    if model:
        row["model"] = _short(model)
    else:
        row["model"] = claude.MODELS[tier] if tier else claude.TICK_MODEL.get(kind)
        row["estimated"].append("model")
    row["tier"] = tier or (TIER_OF.get(row["model"]) if kind == "worker" else None)
    return row


def _receipts(st: dict) -> list[tuple[dict, object]]:
    d = paths.run_dir(st["repo"], st["slug"]) / "done"
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            r = json.loads(paths.read_regular(f, RECEIPT_LIMIT))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict):
            out.append((r, f.with_suffix(".log")))
    out.sort(key=lambda p: num(p[0].get("end")) or 0)
    return out


def build(st: dict) -> list[dict]:
    """Every step of the run, in time order, numbered from 1."""
    attempts: dict = {}
    rows = [row for r, log in _receipts(st) if (row := _row(r, log, attempts))]
    rows += merges(st) + resumes(st) + waits(st)
    rows.sort(key=lambda r: r["start"] or r["end"] or "~")     # no time: last
    for i, r in enumerate(rows, 1):
        r["step"] = i
    return rows


def merges(st: dict) -> list[dict]:
    """A merge happens inside the loop: its only trace is the event. The state keeps 200 events, so a merged
    job whose event fell out still gets its row, without a time."""
    rows, seen = [], set()
    for e in st.get("events") or []:
        m = MERGED.match(e)
        if m and m.group(2) in st["jobs"] and m.group(2) not in seen:
            seen.add(m.group(2))
            t = ts(m.group(1))
            row = blank("merge", m.group(2), t, t)
            row.update(model="script", outcome="merged", minutes=None)
            rows.append(row)
    for jid, j in st["jobs"].items():
        if j.get("status") == "merged" and jid not in seen:
            row = blank("merge", jid, None, None)
            row.update(model="script", outcome="merged", estimated=["time"])
            rows.append(row)
    return rows


def resumes(st: dict) -> list[dict]:
    """One row per automatic resume after an API failure, read from the events (the kept 200)."""
    rows = []
    for e in st.get("events") or []:
        m = RESUMED.match(e)
        if m and m.group(2) in st["jobs"]:
            t = ts(m.group(1))
            row = blank("resume", m.group(2), t, t)
            row.update(model="script", outcome="resumed", reason=_text(m.group(3), 300), minutes=None)
            rows.append(row)
    return rows


def waits(st: dict) -> list[dict]:
    """The time each question waited for Marco. An answer from before P4 has no `asked`: the event says it."""
    asked = {}
    for e in st.get("events") or []:
        m = ASKED.match(e)
        if m:
            asked.setdefault(m.group(2), m.group(1))
    rows = []
    for a in st.get("answers") or []:
        if not isinstance(a, dict):
            continue
        start = a.get("asked") or asked.get(a.get("id"))
        row = blank("wait-marco", a.get("job"), ts(start), ts(a.get("at")))
        row.update(outcome="answered", reason=_text(a.get("kind"), 40))
        if row["minutes"] is None:
            row["estimated"].append("time")
        rows.append(row)
    for q in st.get("questions") or []:
        if isinstance(q, dict):
            row = blank("wait-marco", q.get("job"), ts(q.get("asked")), None)
            row.update(outcome="open", reason=_text(q.get("kind"), 40))
            rows.append(row)
    return rows


def write(st: dict, rows: list[dict]):
    """runs/<repo>/<slug>/steps.jsonl, replaced whole: a link planted at the name is replaced, not followed."""
    path = paths.run_dir(st["repo"], st["slug"]) / "steps.jsonl"
    run._atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return path
