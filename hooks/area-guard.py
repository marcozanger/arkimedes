#!/usr/bin/env python3
"""PreToolUse (Write|Edit|MultiEdit|NotebookEdit) for runner processes.

Workers may write only inside the job's area (ARK_AREA, globs relative to ARK_WORKTREE) and their
report (ARK_REPORT); reviewers write nothing. Fails closed for those roles: what it cannot check
is denied. Any role other than worker may not write, and .git and .claude/settings*.json are never
writable, whatever the area says. Without ARK_ROLE (Marco's sessions) it does nothing."""
from __future__ import annotations  # the hook may run on the system python, 3.9 on macOS

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
CLAUDE_SETTINGS = re.compile(r"\.claude/settings[^/]*\.json")


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
    if not isinstance(tool, str):
        deny("area-guard: no tool name.")
    if tool not in WRITE_TOOLS:
        return
    if role == "reviewer":
        deny("Reviewers do not edit files. Put the change in a finding.")
    if role != "worker":
        deny(f"area-guard: role {role!r} may not write files.")
    ti = data.get("tool_input") or {}
    given = [ti[k] for k in ("file_path", "notebook_path") if k in ti]
    if not given or not all(isinstance(p, str) and p for p in given):
        deny("area-guard: no file path in this call.")
    if not os.environ.get("ARK_WORKTREE"):
        deny("area-guard: no worktree set.")
    for p in given:
        check_path(p, data)


def _grants() -> list:
    """The job's live grants (ARK_GRANTS_FILE: a JSON list of paths the runner keeps current). No file, or
    anything but a well-formed list, means no grants: a bad file can only narrow the area, never open it."""
    f = os.environ.get("ARK_GRANTS_FILE")
    if not f:
        return []
    try:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list) or not all(isinstance(g, str) and g for g in data):
        return []
    return data


def check_path(p: str, data: dict) -> None:
    wt = Path(os.path.realpath(os.environ["ARK_WORKTREE"]))
    target = Path(os.path.realpath(os.path.join(data.get("cwd") or wt, os.path.expanduser(p))))
    report = os.environ.get("ARK_REPORT")
    if report and target == Path(os.path.realpath(report)):
        return
    try:
        rel = target.relative_to(wt).as_posix()
    except ValueError:
        deny(f"{target} is outside your worktree {wt}. Work only there.")
    if ".git" in rel.lower().split("/") or CLAUDE_SETTINGS.fullmatch(rel.lower()):
        deny(f"{rel} is never writable by a worker. Note what you need under Open issues in your report.")
    from ark.globs import match_any
    area = json.loads(os.environ["ARK_AREA"])
    if not isinstance(area, list) or not area or not all(isinstance(g, str) and g for g in area):
        deny("area-guard: the job area is not a list of globs.")
    if match_any(rel, area) or match_any(rel, _grants()):
        return
    from ark.companions import is_companion
    if os.environ.get("ARK_COMPANIONS") != "0" and is_companion(rel):
        return  # the runner checks afterwards that these are few, small and in no other job's area
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
