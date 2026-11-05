#!/usr/bin/env python3
"""PreToolUse (Bash) for every runner process (ARK_ROLE set): no merging, reviewing, creating or
editing PRs, no `gh api`, no `git push`. Only ark-loop does those, in code, after ark-merge's
checks (§7). Fails closed for runner roles; nothing for Marco's sessions.

The command is tokenized like a shell would (quotes respected, operators split it into simple
commands), so `git -c k=v push`, `git --git-dir x push`, `gh -R o/r pr merge` and
`bash -c 'git push'` are caught, while `git commit -m 'push the button'` is not. A command that
cannot be tokenized is denied."""
import json
import os
import shlex
import sys

PR_ACTIONS = {"merge", "review", "create", "edit", "close", "ready", "comment"}
GIT_OPT_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
                      "--config-env"}
GH_OPT_WITH_VALUE = {"-R", "--repo", "--hostname"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
WRAPPERS = {"command", "exec", "env", "sudo", "nohup", "time", "builtin"}
OPERATORS = set(";&|()<>\n")


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason + ". Commit and finish; "
                                             "the runner pushes, opens and merges the PR."}}))
    sys.exit(0)


def _simple_commands(cmd: str) -> list[list[str]]:
    lex = shlex.shlex(cmd.replace("`", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    out: list[list[str]] = [[]]
    for tok in lex:
        if tok and set(tok) <= OPERATORS:
            out.append([])
        else:
            out[-1].append(tok)
    return [c for c in out if c]


def _subcommand(args: list[str], with_value: set[str]) -> list[str]:
    """The args from the first non-option word on (options and their values skipped)."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in with_value else 1
    return args[i:]


def forbidden(cmd: str, depth: int = 0) -> str | None:
    if depth > 4:
        return "commands nested too deep"
    for words in _simple_commands(cmd):
        i = 0   # skip env assignments and wrappers, to reach the program
        while i < len(words) and (("=" in words[i] and not words[i].startswith("-")) or words[i] in WRAPPERS):
            i += 1
        if i >= len(words):
            continue
        prog, args = os.path.basename(words[i]), words[i + 1:]
        if prog == "git":
            sub = _subcommand(args, GIT_OPT_WITH_VALUE)
            if sub and sub[0] == "push":
                return "pushing is the runner's job"
        elif prog == "gh":
            sub = _subcommand(args, GH_OPT_WITH_VALUE)
            if sub and sub[0] == "api":
                return "gh api is not available to runner processes"
            if sub and sub[0] == "pr" and len(sub) > 1 and sub[1] in PR_ACTIONS:
                return "PRs are the runner's job"
        elif prog in SHELLS or prog in ("eval", "xargs"):
            for a in args:
                if any(c in a for c in " ;&|"):
                    why = forbidden(a, depth + 1)
                    if why:
                        return why
            if prog == "xargs":
                why = forbidden(" ".join(shlex.quote(a) if " " in a else a for a in args), depth + 1)
                if why:
                    return why
    return None


def main() -> None:
    if not os.environ.get("ARK_ROLE"):
        return
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            deny("merge-guard: unreadable input")
        if data.get("tool_name") != "Bash":
            return
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str):
            deny("merge-guard: no command")
        why = forbidden(cmd)
        if why:
            deny(why)
    except SystemExit:
        raise
    except Exception as e:
        deny(f"merge-guard could not check this call ({type(e).__name__})")


if __name__ == "__main__":
    main()
