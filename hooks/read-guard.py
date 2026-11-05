#!/usr/bin/env python3
"""PreToolUse (Read|Grep|Glob|Bash) for workers and reviewers: no reading plans or approvals.

The brief is the whole plan a worker gets (§6). Fails closed for those roles; nothing for others."""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

REASON = ("Workers and reviewers do not read plans or approvals: your brief is the whole plan. "
          "If something is missing from it, say so in your report.")


def deny(reason: str = REASON) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def check(raw: str) -> None:
    from ark import paths
    home = Path(os.path.realpath(paths.ark_home()))
    closed = [home / "desk", home / "approvals"]
    data = json.loads(raw)
    if not isinstance(data, dict):
        deny("read-guard: unreadable input.")
    tool, ti = data.get("tool_name"), data.get("tool_input") or {}
    if tool == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            deny("read-guard: no command.")
        names = [str(c) for c in closed] + ["arkimedes/desk", "arkimedes/approvals"]
        if any(n in cmd for n in names):
            deny()
        # `cd <home> && cat desk/...`: the ark folder together with a closed folder's name
        if (str(home) in cmd or "arkimedes" in cmd) and re.search(r"\b(desk|approvals)\b", cmd):
            deny()
        return
    if tool not in ("Read", "Grep", "Glob"):
        return
    p = ti.get("file_path") or ti.get("path")
    if p is None:
        return
    if not isinstance(p, str):
        deny("read-guard: bad path.")
    target = Path(os.path.realpath(os.path.join(data.get("cwd") or os.getcwd(), os.path.expanduser(p))))
    if any(target == c or c in target.parents for c in closed):
        deny()


def main() -> None:
    if os.environ.get("ARK_ROLE") not in ("worker", "reviewer"):
        return
    try:
        check(sys.stdin.read())
    except SystemExit:
        raise
    except Exception as e:
        deny(f"read-guard could not check this call ({type(e).__name__}); it is denied.")


if __name__ == "__main__":
    main()
