#!/usr/bin/env python3
"""User-level PreToolUse(Write|Edit|NotebookEdit|Bash): protect the Arkimedes desk (spec §2, §7).

- approvals/ is written only by `ark approve` (a Python process, not a tool call): every tool
  write there is denied, in every session.
- desk/ is read-only for runner processes (ARK_ROLE set by ark-loop / ark-dispatch).
- Approving must go through `ark approve`, which has an ask permission rule; calling the CLI
  module directly is denied.
Shell detection is a heuristic: a command that names a protected path and looks like a write.
"""
import json
import os
import re
import sys
from pathlib import Path

HOME = Path(os.environ.get("ARK_HOME") or Path.home() / ".claude" / "arkimedes").expanduser()
WRITE = re.compile(
    r"(?<![0-9&])>(?!&)"
    r"|\b(tee|cp|mv|rm|rmdir|touch|mkdir|ln|truncate|dd|install|python3?|node|perl|ruby)\b"
    r"|\bsed\s+-\w*i")
APPROVE_BYPASS = re.compile(r"ark[./]cli(\.py)?\b.*\bapprove\b")


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def under(path: Path, base: Path) -> bool:
    p, b = path.resolve(), base.resolve()
    return p == b or b in p.parents


def mentions(cmd: str, name: str, cwd: str) -> bool:
    pats = [re.escape(str(HOME / name)), re.escape(str(HOME.resolve() / name)),
            re.escape(f"arkimedes/{name}"), r"ARK_HOME\}?/" + name]
    if cwd and Path(cwd).resolve() == HOME.resolve():
        pats.append(rf"(^|[\s\"'=(])(\./)?{name}(/|[\s\"')]|$)")
    return any(re.search(p, cmd) for p in pats)


def main() -> None:
    d = json.load(sys.stdin)
    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}
    cwd = d.get("cwd") or ""
    runner = bool(os.environ.get("ARK_ROLE"))
    protected = [("approvals", True,
                  "Only `ark approve <repo>/<slug>` writes approvals/, and Marco confirms it."),
                 ("desk", runner,
                  "Runner processes (ARK_ROLE) never write desk/: plans belong to Marco's planning sessions.")]
    if tool in ("Write", "Edit", "NotebookEdit"):
        f = ti.get("file_path") or ti.get("notebook_path") or ""
        if not f:
            return
        p = Path(f).expanduser()
        if not p.is_absolute():
            p = Path(cwd or ".") / p
        for name, on, reason in protected:
            if on and under(p, HOME / name):
                deny(reason)
    elif tool == "Bash":
        cmd = ti.get("command") or ""
        if APPROVE_BYPASS.search(cmd):
            deny("Approve with `ark approve <repo>/<slug>`: it is the only way, and Marco confirms it.")
        for name, on, reason in protected:
            if on and mentions(cmd, name, cwd) and WRITE.search(cmd):
                deny(reason + " (This command names that folder and looks like a write.)")


if __name__ == "__main__":
    main()
