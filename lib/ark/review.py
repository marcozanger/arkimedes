"""`ark review`: Marco reviews Euklides' decisions one at a time, with no model, and his verdicts are
distilled into the lessons Euklides reads."""
from __future__ import annotations

import argparse

from . import amend, euklides, run, runreview

PROMPT = "ok · mal: <comment> · skip · q > "


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ark review")
    _arguments(p)
    return p


def _arguments(p) -> None:
    p.add_argument("target", nargs="?", help="<repo>/<slug>: only that run's decisions")
    p.add_argument("--all", action="store_true", help="also decisions already reviewed")
    p.add_argument("--no-distill", action="store_true", help="do not distill the lessons afterwards")


def _outcome(d: dict) -> str:
    try:
        st = run.load(d["repo"], d["slug"])
    except Exception:
        st = None
    if d["job"] == euklides.RUN and isinstance(st, dict):  # a run-level decision: the run's own outcome
        return st.get("status") if st.get("status") in ("done", "blocked") else "still running"
    j = (st or {}).get("jobs", {}).get(d["job"]) if isinstance(st, dict) else None
    if not isinstance(j, dict):
        return "run gone"
    return j.get("status") if j.get("status") in ("merged", "blocked") else "still running"


def pending(rows: list[dict], reviewed: set, show_all: bool, target: str | None) -> list[dict]:
    out = [d for d in rows if show_all or (d.get("id") not in reviewed and d.get("marco") is None)]
    if target:
        out = [d for d in out if f"{d.get('repo')}/{d.get('slug')}" == target]
    return sorted(out, key=lambda d: d.get("at") or "")


def _state(d: dict) -> dict | None:
    try:
        st = run.load(d["repo"], d["slug"])
    except Exception:
        return None
    return st if isinstance(st, dict) else None


def _amendments_of(d: dict) -> list[dict]:
    """The amendments a decision made (by its journal id), so Marco reviews what changed, not only the words."""
    st = _state(d)
    return [a for a in (st or {}).get("amendments") or [] if a.get("decision") == d.get("id")]


def card(d: dict, outcome: str, amendments: list[dict] | None = None) -> str:
    lines = [f"{d['id']} · {d.get('at', '')}",
             f"run: {d['repo']}/{d['slug']} · job: {d['job']} · trigger: {d['trigger']} · outcome: {outcome}",
             f"situation: {d.get('situation', '')}",
             f"decided: {d['action']} — {d.get('note', '')}"]
    if d.get("raise_usd") is not None:
        lines.append(f"raised: ${d['raise_usd']:.2f}")
    if d.get("diagnosis"):
        lines.append(f"diagnosis: {d['diagnosis'].get('cause', '')} → {d['diagnosis'].get('change', '')}")
    for a in amendments or []:
        lines.append(f"amended: {amend.line(a)}")
    if d.get("undo"):
        lines.append(f"undo: {d['undo']} — {d.get('why', '')}")
    if d.get("proposal"):
        lines.append(f"proposal: {d['proposal']}")
    if d.get("marco") is not None:
        lines.append(f"marco answered: {d['marco']}")
    if d.get("valid") is False:
        lines.append("(Euklides gave no usable answer; the runner asked Marco)")
    return "\n".join(lines)


def _verdict(reply: str):
    """('ok', ''), ('wrong', comment), 'skip', 'q', or None for anything else."""
    reply = reply.strip()
    if reply in ("ok", "skip", "q"):
        return ("ok", "") if reply == "ok" else reply
    for word in ("mal:", "wrong:"):
        if reply.startswith(word) and reply[len(word):].strip():
            return "wrong", reply[len(word):].strip()
    return None


def run_review(args, *, ask=input, say=print, model=None) -> int:
    reviewed = {r.get("id") for r in euklides.reviews()}
    items = pending(euklides.decisions(), reviewed, args.all, args.target)
    if args.target and "/" in args.target:   # the run's own decisions file comes after Euklides' decisions
        repo, _, slug = args.target.partition("/")
        state = runreview.load(repo, slug)
        items += [{"run_item": True, "id": f"{args.target}/{i['id']}", "key": i["id"], "repo": repo, "slug": slug,
                   "job": i["job"], "text": i["text"]} for i in state["items"] if args.all or i["verdict"] is None]
        st = _state({"repo": repo, "slug": slug})
        if st is not None:
            say("Amendments to the approved plan:\n" + amend.markdown(st))
    if not items:
        say("Nothing to review.")
        return 0
    new = 0
    for i, d in enumerate(items, 1):
        shown = (f"{d['id']}\nrun: {d['repo']}/{d['slug']} · job: {d['job']} · from decisions.md\n{d['text']}"
                 if d.get("run_item") else card(d, _outcome(d), _amendments_of(d)))
        say(f"\n[{i}/{len(items)}]\n" + shown + "\n" + PROMPT.strip(" >"))
        v = None
        while v is None:
            try:
                v = _verdict(ask(PROMPT))
            except EOFError:
                v = "q"
            if v is None:
                say(PROMPT.strip(" >"))
        if v == "q":
            break
        if v == "skip":
            continue
        if d.get("run_item"):
            runreview.save(d["repo"], d["slug"], d["key"], "ok" if v[0] == "ok" else "change", v[1])
        else:
            euklides.add_review(d["id"], v[0], v[1])
        new += 1
    say(f"\n{new} verdict(s) recorded.")
    if new and not args.no_distill:
        from . import distill
        return distill.run_distill(say=say, model=model)
    return 0


def cmd_review(args) -> int:
    return run_review(args)


def register(sub) -> None:
    s = sub.add_parser("review", help="review Euklides' decisions (ok / mal: <comment>)")
    _arguments(s)
    s.set_defaults(fn=cmd_review)
