"""Amendments: what a run changed about its approved plan while it ran, kept beside the plan, not in it.

The approved plan and its sha stay as Marco approved them. Each change the runner made on Euklides' or
Marco's word, or on its own (companion files), is one amendment in the run's state:

    {id: "A<n>", ts, job, kind, before, after, why, by, decision, undo}

kind: `area` (files joined a job's area), `budget` (a job's budget grew), `gate` (a head was accepted on a
replacement gate), `base` (a fix(base) commit landed on the integration branch). `by` is Euklides, Marco or
the runner; `decision` the Euklides journal id when there is one. The effective plan is the approved plan
with its amendments applied (`effective`); `ark review`, the report, `ark ui` and `ark diff --effective`
show them."""
from __future__ import annotations

from . import run

KINDS = ("area", "budget", "gate", "base")
WHY_CHARS = 600


def add(st: dict, job: str, kind: str, before, after, *, why: str, by: str, decision: str | None = None,
        undo: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"amendment kind {kind!r}")
    rows = st.setdefault("amendments", [])
    a = {"id": f"A{len(rows) + 1}", "ts": run.now(), "job": job, "kind": kind, "before": before, "after": after,
         "why": " ".join(str(why or "").split())[:WHY_CHARS], "by": by, "decision": decision, "undo": undo}
    rows.append(a)
    return a


def of(st: dict, job: str | None = None, kind: str | None = None) -> list[dict]:
    return [a for a in st.get("amendments") or []
            if (job is None or a["job"] == job) and (kind is None or a["kind"] == kind)]


def effective(manifest: dict, st: dict) -> dict:
    """Per job of the approved manifest: its area, budget and gate as the run has them now."""
    out = {}
    for mj in manifest.get("jobs") or []:
        jid = mj["id"]
        j = (st.get("jobs") or {}).get(jid) or {}
        grants = (st.get("grants") or {}).get(jid) or []
        gates = [a["after"] for a in of(st, jid, "gate")]
        out[jid] = {"area": list(mj.get("area") or []) + [g for g in grants if g not in (mj.get("area") or [])],
                    "budget": round(mj["budget"]["usd"] + j.get("usd_raised", 0), 4),
                    "gate": gates[-1] if gates else mj.get("gate")}
    return out


def _show(v) -> str:
    if v is None or v == []:
        return "—"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, float):
        return f"${v:.2f}"
    return str(v)


def line(a: dict) -> str:
    """One amendment as one line of text."""
    who = a["by"] + (f" ({a['decision']})" if a.get("decision") else "")
    return (f"{a['id']} · {a['job']} · {a['kind']}: {_show(a['before'])} → {_show(a['after'])} · {who}"
            + (f" — {a['why']}" if a.get("why") else ""))


def effective_text(manifest: dict, st: dict) -> str:
    """`ark diff --effective`: per job, what its area, budget and gate became, then every amendment."""
    eff, lines = effective(manifest, st), [f"# Effective plan · {st.get('repo')}/{st.get('slug')}", ""]
    for mj in manifest.get("jobs") or []:
        e, changes = eff[mj["id"]], []
        added = [a for a in e["area"] if a not in (mj.get("area") or [])]
        if added:
            changes.append(f"  area  + {', '.join(added)}")
        if e["budget"] != mj["budget"]["usd"]:
            changes.append(f"  budget  ${mj['budget']['usd']:.2f} → ${e['budget']:.2f}")
        if e["gate"] != mj.get("gate"):
            changes.append(f"  gate  {mj.get('gate')} → {e['gate']} (one head only)")
        lines.append(f"{mj['id']}: " + ("as approved" if not changes else ""))
        lines += changes
    bases = of(st, kind="base")
    if bases:
        lines += ["", "Base fixes on the integration branch:"] + [f"  {line(a)}" for a in bases]
    return "\n".join(lines + ["", "## Amendments", "", markdown(st)]) + "\n"


def markdown(st: dict) -> str:
    rows = st.get("amendments") or []
    if not rows:
        return "No amendments: the run kept to its approved plan."
    return "\n".join(f"- {line(a)}" for a in rows)
