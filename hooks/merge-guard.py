#!/usr/bin/env python3
"""PreToolUse (Bash) for every runner process (ARK_ROLE set): no merging, reviewing, creating or
editing PRs, no `gh api`, no `git push`. Only ark-loop does those, in code, after ark-merge's
checks (§7). Fails closed for runner roles; nothing for Marco's sessions.

It does not parse shell, because a list of forms always misses some. It asks for proof instead:
1. ALWAYS_DENY, with and without quotes: text sent to a shell or a git subcommand no static check
   can read. Denied as is.
2. No MENTION of a push, a merge or gh anywhere in the raw text: allowed.
3. FORBIDDEN: the plain regex on the raw text, with only literal quoted strings blanked.
4. Otherwise a mention is allowed only if every byte is accounted for (_scan) and every simple
   command is on a short list of programs that treat their arguments as data (_harmless).
A false denial costs the worker one retry; a false allow can move main.

Static checks have limits: a command with no mention at all (`./script.sh`, a base64 payload for
`python3 -c`) passes. The push layer in claude.runner_env and branch protection are the backstops."""
from __future__ import annotations  # the hook may run on the system python, 3.9 on macOS

import json
import os
import re
import sys

PR_VERBS = "merge|review|create|edit|close|ready|comment"
SHELL = r"(?:\S*/)?(?:ba|z|da|k|fi|c|tc)?sh"
_VALUE_OPT = "-C|-c|--git-dir|--work-tree|--namespace|--exec-path|--super-prefix|--config-env|--attr-source"
GIT_OPTIONS = (rf"(?:\s+(?:{_VALUE_OPT})\s+(?:\"[^\"]*\"|'[^']*'|\S+)"
               rf"|\s+(?!(?:{_VALUE_OPT})\s)-\S+)*")

ALWAYS_DENY = re.compile("|".join([
    # a shell or an interpreter reading its program from stdin
    rf"\|&?\s*(?:(?:sudo|env|command|exec|nohup|nice|time|builtin)\s+)*{SHELL}(?=[\s;&|)]|$)",
    r"\|&?\s*(?:\S*/)?(?:python[\d.]*|perl|ruby|node|php)(?:\s+-)?\s*(?:[;&|)\n]|$)",
    rf"(?:^|[\s;&|(])(?:{SHELL}(?:\s+-\S+)*|source|\.)\s*(?:<|/dev/stdin)",
    # a shell whose whole program is an expansion
    rf"(?:^|[\s;&|(]){SHELL}(?:\s+-\S+)*\s+-\w*c\w*\s+[\"]?[$`]",
    # eval where a command starts
    r"(?:^|[;&|({\n!]|\b(?:then|do|else|command|builtin|exec|env|time|nohup|nice)\s)\s*eval(?=[\s;]|$)",
    # a git subcommand decided at run time: expansion or glob
    rf"\bgit{GIT_OPTIONS}\s+(?!-)[\"']?\S*[$`*?\[{{]",
]))
# a word followed by `/` or an extension is a path (push.dart), except a shell script that may do it
MENTION = re.compile(r"(?<![\w-])(?:push|merge)(?![\w/-]|\.(?!(?:ba|z)?sh\b)\w)|\bgh(?![\w-])"
                     r"|git-push|http-push|send-pack|receive-pack"
                     r"|pushurl|insteadof|set-url|remote\.\S+\.(?:url|mirror)\b|GIT_CONFIG_|alias\.", re.I)
FORBIDDEN = [
    (re.compile(rf"\bgh\b[^;&|\n]*\bpr\b[^;&|\n]*\b(?:{PR_VERBS})\b", re.I), "PRs are the runner's job"),
    (re.compile(r"\bgh\b[^;&|\n]*\bapi\b", re.I), "gh api is not available to runner processes"),
    (re.compile(rf"\bgit{GIT_OPTIONS}\s+push\b|git-push|http-push|send-pack|receive-pack", re.I),
     "pushing is the runner's job"),
]

# `git commit -m "$(cat <<'EOF' ... EOF\n)"`: the delimiter is quoted, so the body is literal
COMMIT_MESSAGE = r"\"\$\(cat <<'(?P<d>\w+)'\n(?:(?!(?P=d)\n)[^\n]*\n)*(?P=d)\n\s*\)\""
PLAIN_DOUBLE_QUOTED = r"\"[^\"$`\\]*\""
_COMMIT_MESSAGE_RX = re.compile(COMMIT_MESSAGE)
_PLAIN_DOUBLE_RX = re.compile(PLAIN_DOUBLE_QUOTED)
_LITERALS = re.compile(rf"'[^']*'|{COMMIT_MESSAGE}|{PLAIN_DOUBLE_QUOTED}")
_NULL_REDIRECT = re.compile(r"(?<!\S)(?:2>&1|[12]?>\s*/dev/null)(?!\S)")
OPAQUE = set("$`\\<>(){}#")
MAX_MENTION_LENGTH = 20_000    # FORBIDDEN is quadratic on `gh pr pr pr ...`: about 2 s here

READERS = {"cat", "cd", "popd", "pushd", "pwd", "ls", "head", "tail", "wc", "sort", "uniq", "cut", "tr", "diff", "jq",
           "grep", "egrep", "fgrep", "rg", "ag", "ack", "echo", "printf", "true", "false", "test", "find", "stat",
           "file", "basename", "dirname"}
READER_BAD_ARGS = ("-exec", "-ok", "-delete", "-fprint", "-fls", "--pre", "--compress-program", "--pager")
XARGS_FLAGS = {"-0", "-r", "--null", "--no-run-if-empty"}
GIT_SUBCOMMANDS = {"status", "log", "show", "diff", "grep", "blame", "shortlog", "rev-parse", "rev-list", "ls-files",
                   "ls-tree", "cat-file", "describe", "branch", "reflog", "help", "version", "merge-base", "show-ref",
                   "for-each-ref", "name-rev", "check-ignore", "add", "commit", "stash", "restore", "rm", "mv",
                   "checkout", "switch", "merge", "fetch", "reset", "revert", "cherry-pick", "apply", "clean"}
# options of those subcommands that run a program or a pager
GIT_BAD_ARGS = ("-O", "--open-files-in-pager", "--upload-pack", "--receive-pack", "--exec", "-x", "--ext-diff")
# options whose value is text, not something to run: `-mVALUE`, `-eVALUE`, `--name=VALUE`
TEXT_OPTIONS = ("-m", "-e", "--message=", "--grep=", "--format=", "--pretty=", "--author=", "--regexp=", "--include=",
                "--exclude=")
GH_READS = {("pr", "view"), ("pr", "list"), ("pr", "checks"), ("pr", "diff"), ("pr", "status"), ("run", "view"),
            ("run", "list"), ("issue", "view"), ("issue", "list"), ("repo", "view"), ("auth", "status")}


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def _scan(cmd: str) -> list[list[str]] | None:
    """The simple commands of `cmd` as lists of words, or None if a byte is not plainly accounted
    for: only separators (space, tab, newline, ; & |), literal quotes and ordinary characters."""
    commands: list[list[str]] = [[]]
    word = None
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "'":
            end = cmd.find("'", i + 1)
            if end < 0:
                return None
            word = (word or "") + cmd[i + 1:end]
            i = end + 1
        elif c == '"':
            m = _COMMIT_MESSAGE_RX.match(cmd, i)
            if m:
                word = (word or "") + "MESSAGE"
            else:
                m = _PLAIN_DOUBLE_RX.match(cmd, i)
                if not m:
                    return None
                word = (word or "") + m.group()[1:-1]
            i = m.end()
        elif c in " \t\n;&|":
            if word is not None:
                commands[-1].append(word)
                word = None
            if c not in " \t" and commands[-1]:
                commands.append([])
            i += 1
        elif c in OPAQUE:
            return None
        else:
            word = (word or "") + c
            i += 1
    if word is not None:
        commands[-1].append(word)
    return [c for c in commands if c]


def _option_ok(word: str, bad: tuple[str, ...]) -> bool:
    """Not an option that runs a program, spelled in full or abbreviated (git and GNU tools accept
    any unique stem, so the value may also be the next word), and not an unlisted option holding
    a mention, which may be a program bundled behind short flags (`-nO'git push'`)."""
    if not word.startswith("-") or word == "--":
        return True
    name = word.split("=", 1)[0]
    if word.startswith(bad) or (name.startswith("--") and any(b.startswith(name) for b in bad)):
        return False
    return not MENTION.search(word) or word.startswith(TEXT_OPTIONS)


def _reader_ok(words: list[str]) -> bool:
    return words[0] in READERS and all(_option_ok(w, READER_BAD_ARGS) for w in words[1:])


def _xargs_ok(args: list[str]) -> bool:
    i = 0
    while i < len(args) and args[i] in XARGS_FLAGS:
        i += 1
    return i < len(args) and _reader_ok(args[i:])


def _git_ok(args: list[str]) -> bool:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-C", "--git-dir", "--work-tree"):
            i += 2
        elif args[i] in ("--no-pager", "-P") or args[i].startswith(("--git-dir=", "--work-tree=")):
            i += 1
        else:
            return False
    if i >= len(args):
        return False
    sub, rest = args[i], args[i + 1:]
    if sub == "config":     # reading one key is fine; writing one is not
        return (len(rest) == 1 and (rest[0] in ("-l", "--list") or not rest[0].startswith("-"))
                or len(rest) == 2 and rest[0] in ("--get", "--get-all"))
    return sub in GIT_SUBCOMMANDS and all(_option_ok(a, GIT_BAD_ARGS) for a in rest)


def _gh_ok(args: list[str]) -> bool:
    positional: list[str] = []
    i = 0
    while i < len(args) and len(positional) < 2:
        if args[i] in ("-R", "--repo", "--hostname"):
            i += 2
        elif args[i].startswith(("--repo=", "--hostname=")):
            i += 1
        elif args[i].startswith("-"):
            return False
        else:
            positional.append(args[i])
            i += 1
    return tuple(positional) in GH_READS


def _harmless(words: list[str]) -> bool:
    prog, args = words[0], words[1:]
    if prog == "git":
        return _git_ok(args)
    if prog == "gh":
        return _gh_ok(args)
    if prog == "xargs":
        return _xargs_ok(args)
    return _reader_ok(words)


def _without_commit_messages(cmd: str) -> str:
    return _LITERALS.sub(lambda m: '"MESSAGE"' if m.group().startswith('"$(cat') else m.group(), cmd)


def _unquoted(text: str) -> str:
    return re.sub(r"['\"\\]", "", text.replace("\\\n", ""))


def forbidden(cmd: str) -> str | None:
    shape = _without_commit_messages(cmd)
    if ALWAYS_DENY.search(shape) or ALWAYS_DENY.search(_unquoted(shape)):
        return ("this runs text no check can read (eval, a shell or interpreter reading stdin, a git subcommand "
                "built at run time). This is not a stop signal: write the command out plainly and retry, or put "
                "the text in a file with the Write tool")
    if not MENTION.search(_unquoted(cmd)):
        return None
    if len(cmd) <= MAX_MENTION_LENGTH:
        blanked = _LITERALS.sub("''", cmd)
        for rx, why in FORBIDDEN:
            if rx.search(blanked):
                return why + ". Commit and finish; the runner pushes, opens and merges the PR"
        commands = _scan(_NULL_REDIRECT.sub(" ", cmd))
        if commands is not None and all(_harmless(c) for c in commands):
            return None
    return ("this command mentions push, merge or gh and merge-guard cannot prove it harmless. This is not a stop "
            "signal: if you are not pushing, retry with one plain command (grep, cat, echo, git log, git commit, "
            "gh pr view; literal quotes; no $(), redirects, env prefixes), put the text in a file with the Write "
            "tool, or leave the word out. Pushing and merging are the runner's job")


def main() -> None:
    if not os.environ.get("ARK_ROLE"):
        return
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict) or not isinstance(data.get("tool_name"), str):
            deny("merge-guard: unreadable input")
        if data["tool_name"] != "Bash":
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
        deny(f"merge-guard could not check this call ({type(e).__name__}); it is denied, retry a simpler command")


if __name__ == "__main__":
    main()
