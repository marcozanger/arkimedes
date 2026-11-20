"""`ark replay`: Euklides' past decisions read again under the blast-radius rules.

A report, not a test and not a decision: it reads history/euklides/decisions.jsonl, sorts every
question Euklides put to Marco by what it was about, and says whether blast-radius Euklides would
have decided it alone or whether it touches the reserved list (R1-R6). The reading is by keywords
over the situation and the proposal, so each row is printed for Marco to check by eye.
"""
import re

from . import euklides

# Checked in this order: the first hit names the row. Reserved first, since a reserved hit wins.
RESERVED = [
    ("R1", "what is delivered", re.compile(
        r"add (a|one) (previous |new )?job|agregar un job|new job|drop (the |a )?job|spec scope|"
        r"core change outside|acceptance criteria", re.I)),
    ("R2", "weaker verification", re.compile(
        r"skip|xfail|--grep-invert|accept \S+ (as done )?(at \S+ )?(despite|past the)|"
        r"override the gate|known pre-existing|looser gate|set (the )?(integration_)?gate", re.I)),
    ("R5", "public contract", re.compile(
        r"package\.json|lockfile|pnpm-lock|devDependenc|new dependenc|migration|hh\.d\.ts", re.I)),
    ("R6", "outside the job's branch", re.compile(
        r"GitError|could not run|rev-parse|recreates? ark/", re.I)),
]
OWN = [
    ("budget", "raise (up to 2x the plan)", re.compile(
        r"(add|approve|raise)\b[^.]{0,40}\$\d|\$\d+(\.\d+)? more|raise (its|the job's) budget", re.I)),
    ("area", "widen", re.compile(r"outside (its|my|this job's) area|(to|in) (this|the) job's area|area of|"
                                  r"area hook|the area|grant |may (i|this job) (also )?(edit|change|update|"
                                  r"add|remove|fix|regenerate)", re.I)),
    ("base", "fix_base or a rerun", re.compile(r"without the job too|integration gate failed|flaky|"
                                                r"under (machine )?load|load timeouts|merge origin/main|"
                                                r"does not merge cleanly|test isolation", re.I)),
]
AGREED = re.compile(r"^\s*(ok|si|sí|yes|y|commit|hacelo|approved|follow|go with|land it|accept|raise it|"
                    r"a,|a |done)", re.I)


def _cap(row: dict) -> str | None:
    """A run-cap question within twice the plan is Euklides' own; past it, R4."""
    m = re.search(r"over its planned total of \$([\d.]+)", row.get("situation") or "")
    raised = re.search(r"raise ([\d.]+)", row.get("proposal") or "")
    if not m:
        return None
    planned = float(m.group(1))
    spent = re.search(r"spent \$([\d.]+)", row["situation"])
    after = (float(spent.group(1)) if spent else planned) + (float(raised.group(1)) if raised else 0.0)
    return "own" if after <= 2 * planned else "R4"


def classify(row: dict) -> dict:
    """{"kind": reason, "verdict": "own" | "R1".."R6" | "same" | "?", "would": what Euklides does}."""
    if row.get("action") != "ask":
        return {"kind": row.get("action"), "verdict": "same", "would": "already his own call"}
    if row.get("trigger") == "cap_run":
        v = _cap(row) or "?"
        return {"kind": "budget", "verdict": v, "would": "raise the run" if v == "own" else "ask Marco"}
    text = " ".join(str(row.get(k) or "") for k in ("situation", "proposal"))  # the note argues; it is not the ask
    for code, name, rx in RESERVED:
        if rx.search(text):
            return {"kind": name, "verdict": code, "would": "ask Marco"}
    for kind, would, rx in OWN:
        if rx.search(text):
            return {"kind": kind, "verdict": "own", "would": would}
    return {"kind": "other", "verdict": "?", "would": "read it"}


def agreed(row: dict) -> bool:
    return bool(AGREED.match(row.get("marco") or ""))


def _one(s, n: int = 90) -> str:
    s = " ".join(str(s or "").split()).replace("|", "/")
    return s if len(s) <= n else s[:n - 1] + "…"


def report(rows: list[dict], show_rows: bool = True) -> str:
    asks = [(r, classify(r)) for r in rows if r.get("action") == "ask" and r.get("valid", True)]
    own = [(r, c) for r, c in asks if c["verdict"] == "own"]
    reserved = [(r, c) for r, c in asks if c["verdict"].startswith("R")]
    unknown = [(r, c) for r, c in asks if c["verdict"] == "?"]
    by_kind: dict[str, int] = {}
    for _, c in own:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
    by_code: dict[str, int] = {}
    for _, c in reserved:
        by_code[c["verdict"]] = by_code.get(c["verdict"], 0) + 1
    out = [
        "# Euklides replay under blast radius",
        "",
        f"{len(rows)} decisions, {len(asks)} of them questions to Marco (invalid answers left out).",
        "",
        f"- **His own call now: {len(own)}** ({', '.join(f'{k} {v}' for k, v in sorted(by_kind.items()))}); "
        f"Marco had agreed with the proposal in {sum(agreed(r) for r, _ in own)} of them.",
        f"- **Still Marco's (reserved list): {len(reserved)}** "
        f"({', '.join(f'{k} {v}' for k, v in sorted(by_code.items()))}).",
        f"- **Not sorted, read by eye: {len(unknown)}.**",
        "",
        "The sorting reads keywords, not the case: a widen is his own unless the file belongs to another live "
        "job (R3), which the journal does not record.",
    ]
    if show_rows:
        out += ["", "| # | trigger | verdict | would | situation | Marco said |", "|---|---|---|---|---|---|"]
        for i, (r, c) in enumerate(asks, 1):
            out.append(f"| {i} | {r.get('trigger')} | {c['verdict']} ({c['kind']}) | {c['would']} | "
                       f"{_one(r.get('situation'))} | {_one(r.get('marco'), 40)} |")
    return "\n".join(out) + "\n"


def cmd_replay(args) -> int:
    print(report(euklides.decisions(), show_rows=not args.summary), end="")
    return 0


def register(sub) -> None:
    s = sub.add_parser("replay", help="read Euklides' past questions again under the blast-radius rules")
    s.add_argument("--summary", action="store_true", help="only the counts, no row per question")
    s.set_defaults(fn=cmd_replay)
