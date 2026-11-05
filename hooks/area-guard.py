#!/usr/bin/env python3
"""PreToolUse (Write|Edit|MultiEdit|NotebookEdit) for runner processes.

Workers may write only inside the job's area (ARK_AREA, globs relative to ARK_WORKTREE) and their
report (ARK_REPORT); reviewers write nothing. Fails closed for those roles: what it cannot check
is denied. Any role other than worker may not write. Without ARK_ROLE (Marco's sessions) it does
nothing."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def check(raw: str) -> None:
    role = os.environ.get("ARK_ROLE")
    data = json.loads(raw)
    if not isinstance(data, dict):
        deny("area-guard: unreadable input.")
    tool = data.get("tool_name")
    if tool not in WRITE_TOOLS:
        return
    if role == "reviewer":
        deny("Reviewers do not edit files. Put the change in a finding.")
    if role != "worker":
        deny(f"area-guard: role {role!r} may not write files.")
    ti = data.get("tool_input") or {}
    p = ti.get("file_path") or ti.get("notebook_path")
    if not isinstance(p, str) or not p:
        deny("area-guard: no file path in this call.")
    if not os.environ.get("ARK_WORKTREE"):
        deny("area-guard: no worktree set.")
    wt = Path(os.path.realpath(os.environ["ARK_WORKTREE"]))
    target = Path(os.path.realpath(os.path.join(data.get("cwd") or wt, os.path.expanduser(p))))
    report = os.environ.get("ARK_REPORT")
    if report and target == Path(os.path.realpath(report)):
        return
    try:
        rel = target.relative_to(wt).as_posix()
    except ValueError:
        deny(f"{target} is outside your worktree {wt}. Work only there.")
    from ark.globs import match_any
    area = json.loads(os.environ["ARK_AREA"])
    if not isinstance(area, list) or not area or not all(isinstance(g, str) and g for g in area):
        deny("area-guard: the job area is not a list of globs.")
    if not match_any(rel, area):
        deny(f"{rel} is outside this job's area {area}. Do not change it: note it under Open issues "
             "in your report and, if the job cannot be done without it, end with STATUS: BLOCKED.")


def main() -> None:
    if not os.environ.get("ARK_ROLE"):
        return
    try:
        check(sys.stdin.read())
    except SystemExit:
        raise
    except Exception as e:  # fail closed for workers and reviewers
        deny(f"area-guard could not check this call ({type(e).__name__}); it is denied.")


if __name__ == "__main__":
    main()
