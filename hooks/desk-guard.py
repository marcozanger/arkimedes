#!/usr/bin/env python3
"""User-level PreToolUse(Write|Edit|NotebookEdit|Bash): protect the Arkimedes desk (spec §2, §7).

- approvals/ is written only by `ark approve` (a Python process, not a tool call): every tool
  write there is denied, in every session.
- desk/ is read-only for runner processes (ARK_ROLE set by ark-loop / ark-dispatch).
- Approving must go through exactly `ark approve <repo>/<slug>` (or `<path>/bin/ark approve ...`),
  the forms that have an ask permission rule. Any other way of invoking approve is denied, and a
  runner process may not approve at all.
Shell detection is a heuristic: a command that names a protected path and looks like a write.
The hook never crashes a session: bad input means allow (no output, exit 0).
"""
import json
import os
import re
import sys
from pathlib import Path

HOME = Path(os.environ.get("ARK_HOME") or Path.home() / ".claude" / "arkimedes").expanduser()
WRITE = re.compile(
    r"(?<![0-9&])>(?!&)"
    r"|\b(tee|cp|mv|rm|rmdir|touch|mkdir|ln|truncate|dd|install|python3?|node|perl|ruby"
    r"|chmod|rsync|tar|unzip|patch|sh|bash|zsh|eval|xargs)\b"
    r"|\bsed\s+-\w*i"
    r"|\bgit\b.*\b(checkout|restore|apply|stash|reset|clean)\b")
HEREDOC = re.compile(r"(<<-?\s*(['\"]?)(\w+)\2[^\n]*)\n.*?\n[ \t]*\3[ \t]*(?=\n|$)", re.S)
QUOTED = re.compile(r"(?<=\s)('[^']*'|\"[^\"$`]*\")")  # argument strings only, no $( or ``
APPROVE_CALL = re.compile(
    r"((^|[\s;&|(`'\"/])ark['\"]?\s+approve\b)"
    r"|((\bark\.cli\b|from\s+ark\s+import|cmd_approve).*\bapprove)")
CANONICAL = re.compile(r"\s*(ark|[\w.~$/-]*/bin/ark)\s+approve\s+[\w.@/-]+\s*")
APPROVE_MSG = ("Approve with `ark approve <repo>/<slug>` (or `~/.local/bin/ark approve <repo>/<slug>`) "
               "as the whole command: it is the only way, and Marco confirms it.")


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
    if (cwd and under(Path(cwd), HOME)) or re.search(r"arkimedes|ARK_HOME", cmd):
        pats.append(rf"(^|[\s\"'=(/])(\./)?{name}(/|[\s\"')]|$)")
    return any(re.search(p, cmd) for p in pats)


def strip_heredocs(cmd: str) -> str:
    """Drop heredoc bodies only when `cat` reads them (text); any other consumer may run them."""
    def text_only(m):
        line = cmd[cmd.rfind("\n", 0, m.start()) + 1:m.start()]
        return m.group(1) if re.search(r"\bcat(\s+>\s*\S+)?\s*$", line) else m.group(0)
    return HEREDOC.sub(text_only, cmd)


def main() -> None:
    d = json.load(sys.stdin)
    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}
    cwd = d.get("cwd") or ""
    if not isinstance(ti, dict) or not isinstance(cwd, str):
        return
    runner = "ARK_ROLE" in os.environ
    protected = [("approvals", True,
                  "Only `ark approve <repo>/<slug>` writes approvals/, and Marco confirms it."),
                 ("desk", runner,
                  "Runner processes (ARK_ROLE) never write desk/: plans belong to Marco's planning sessions.")]
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        f = ti.get("file_path") or ti.get("notebook_path") or ""
        if not f or not isinstance(f, str):
            return
        p = Path(f).expanduser()
        if not p.is_absolute():
            p = Path(cwd or ".") / p
        for name, on, reason in protected:
            if on and under(p, HOME / name):
                deny(reason)
    elif tool == "Bash":
        cmd = ti.get("command") or ""
        if not isinstance(cmd, str):
            return
        cmd = strip_heredocs(cmd)
        if APPROVE_CALL.search(cmd) and (runner or not CANONICAL.fullmatch(cmd)):
            deny("Runner processes never approve plans. " + APPROVE_MSG if runner else APPROVE_MSG)
        shell = QUOTED.sub("''", cmd)
        for name, on, reason in protected:
            if on and mentions(cmd, name, cwd) and WRITE.search(shell):
                deny(reason + " (This command names that folder and looks like a write.)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # fail open, never block sessions on a hook bug
        print(f"desk-guard: ignored error: {e!r}", file=sys.stderr)
        sys.exit(0)
