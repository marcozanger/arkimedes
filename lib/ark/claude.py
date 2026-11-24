"""Command lines and environments for every `claude -p` the runner starts.

Hard caps (CLI 2.1.318 has no --max-turns): --max-budget-usd per process, and the wall-clock
deadline per tier that ark-loop enforces by killing the process group."""
from __future__ import annotations

import os
import shutil

from . import accounts, paths

MODELS = {"T0": "haiku", "T1": "sonnet", "T2": "opus"}
TIER_MINUTES = {"T0": 20, "T1": 60, "T2": 120}
TIER_TOKENS = {"T0": 150_000, "T1": 400_000, "T2": 800_000}  # per process: input + cache creation + output
STALL_MINUTES = 20  # no new line in the log for this long: the default only, limits.stall_minutes rules
HARD_FACTOR = 2     # the tier minutes call the intervene tick; the process dies at this many times them
NEXT_TIER = {"T0": "T1", "T1": "T2"}
TICK_MODEL = {"intervene": "opus", "decisions": "sonnet", "distill": "sonnet"}
ROLE_FILES = {"worker": "ark-dev.md", "reviewer": "ark-reviewer.md"}
NO_TOOLS = "Bash,Write,Edit,MultiEdit,NotebookEdit"
DEAD_PUSH_URL = "/dev/null/no-push-from-workers"


def claude_bin() -> str:
    return os.environ.get("ARK_CLAUDE") or shutil.which("claude") or "claude"


def advisor_for(tier: str) -> str | None:
    """The model a worker of `tier` may consult mid-job (workspace.json `advisor`), or None. A tier that is not
    listed, or no model, means no advisor. Its tokens are billed on top of the worker's own."""
    block = paths.load_workspace().get("advisor") or {}
    model = block.get("model")
    return model if model and tier in (block.get("tiers") or []) else None


def agent_cmd(role: str, model: str, prompt: str, *, session: str, resume: bool = False, usd: float,
              tools: list[str] | None = None, denied_tools: list[str] | None = None,
              advisor: str | None = None) -> list[str]:
    code = paths.code_dir()
    cmd = [claude_bin(), "-p", prompt, "--model", model,
           "--settings", str(code / "worker-settings.json"),
           "--permission-mode", "auto",
           "--max-budget-usd", f"{usd:g}"]
    if advisor and role == "worker":
        cmd += ["--advisor", advisor]
    if role in ROLE_FILES:
        cmd += ["--append-system-prompt-file", str(code / "agents" / ROLE_FILES[role]),
                "--output-format", "stream-json", "--verbose"]
    elif role == "euklides":  # reads the worktree and the worker log; never writes, runs or browses
        cmd += ["--append-system-prompt-file", str(code / "agents" / "euklides.md"),
                "--output-format", "json", "--allowedTools", ",".join(tools or []), "--disallowedTools", ",".join(denied_tools or [])]
    else:
        cmd += ["--output-format", "json", "--disallowedTools", NO_TOOLS]
    cmd += ["--resume", session] if resume else ["--session-id", session]
    return cmd


def with_account(env: dict, repo_name: str | None) -> dict:
    """`env` with the Claude account of `repo_name`: its rule's CLAUDE_CONFIG_DIR, or none at all, never one
    inherited from whoever started the loop. Raises accounts.AccountError for a rule that cannot be used."""
    env = {k: v for k, v in env.items() if k != "CLAUDE_CONFIG_DIR"}
    if repo_name:
        env.update(accounts.claude_env(repo_name))
    return env


def runner_env(role: str, *, repo_name: str | None, origin_url: str | None = None, **extra: str) -> dict:
    """The environment of a runner process: never the API key (it would bill per token), never
    the parent's Claude Code session markers; ARK_ROLE switches the guards on.
    `repo_name` picks the Claude account (accounts.claude_env); None, or a repo with no rule, runs on
    the personal one.

    A role with Bash also gets the push layer: for every git under it, a push to `origin_url`
    (from gitops.origin_url) goes to a dead path. It is scoped to the URL, not to the remote's
    name, so a repo's tests can still push to the temp remotes they make. The loop's own git
    never runs under this environment."""
    if role in ROLE_FILES and not origin_url:
        raise ValueError(f"runner_env: role {role} needs origin_url for the push layer")
    env = with_account({k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDECODE")},
                       repo_name)
    env["ARK_ROLE"] = role
    env["ARKIMEDES"] = "1"
    env["ARK_HOME"] = str(paths.ark_home())
    if origin_url:
        n = int(env.get("GIT_CONFIG_COUNT") or 0)
        env["GIT_CONFIG_COUNT"] = str(n + 1)
        env[f"GIT_CONFIG_KEY_{n}"] = f"url.{DEAD_PUSH_URL}.pushInsteadOf"
        env[f"GIT_CONFIG_VALUE_{n}"] = origin_url
    for k, v in extra.items():
        env[f"ARK_{k.upper()}"] = str(v)
    return env
