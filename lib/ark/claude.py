"""Command lines and environments for every `claude -p` the runner starts.

Hard caps (CLI 2.1.318 has no --max-turns): --max-budget-usd per process, and the wall-clock
deadline per tier that ark-loop enforces by killing the process group."""
from __future__ import annotations

import os
import shutil

from . import paths

MODELS = {"T0": "haiku", "T1": "sonnet", "T2": "opus"}
TIER_MINUTES = {"T0": 20, "T1": 60, "T2": 120}
NEXT_TIER = {"T0": "T1", "T1": "T2"}
TICK_MODEL = {"judge": "opus", "decisions": "sonnet"}
ROLE_FILES = {"worker": "ark-dev.md", "reviewer": "ark-reviewer.md"}
NO_TOOLS = "Bash,Write,Edit,MultiEdit,NotebookEdit"


def claude_bin() -> str:
    return os.environ.get("ARK_CLAUDE") or shutil.which("claude") or "claude"


def agent_cmd(role: str, model: str, prompt: str, *, session: str, resume: bool = False, usd: float) -> list[str]:
    code = paths.code_dir()
    cmd = [claude_bin(), "-p", prompt, "--model", model,
           "--settings", str(code / "worker-settings.json"),
           "--permission-mode", "auto",
           "--max-budget-usd", f"{usd:g}"]
    if role in ROLE_FILES:
        cmd += ["--append-system-prompt-file", str(code / "agents" / ROLE_FILES[role]),
                "--output-format", "stream-json", "--verbose"]
    else:
        cmd += ["--output-format", "json", "--disallowedTools", NO_TOOLS]
    cmd += ["--resume", session] if resume else ["--session-id", session]
    return cmd


def runner_env(role: str, **extra: str) -> dict:
    """The environment of a runner process: never the API key (it would bill per token), never
    the parent's Claude Code session markers; ARK_ROLE switches the guards on."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDECODE")}
    env["ARK_ROLE"] = role
    env["ARKIMEDES"] = "1"
    env["ARK_HOME"] = str(paths.ark_home())
    for k, v in extra.items():
        env[f"ARK_{k.upper()}"] = str(v)
    return env
