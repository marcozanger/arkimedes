"""Which Claude account and gh user a repo's runs use.

workspace.json may hold `accounts`, a list of rules:
    {"match": "globallogic/", "claude_config_dir": "~/.claude-work", "gh_user": "marcozanger-gl"}
`match` is a repo-name prefix on folder boundaries; the other two keys are each optional. The first rule
that matches wins. No rule means the personal setup: no CLAUDE_CONFIG_DIR, gh's active account. A rule
that matches but cannot be used is an AccountError: never a fall back to the personal account."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import paths

KEYS = ("match", "claude_config_dir", "gh_user")
GH_TIMEOUT = 30  # seconds for `gh auth token`


class AccountError(Exception):
    pass


def _rules() -> list[dict]:
    rules = paths.load_workspace().get("accounts") or []
    if not isinstance(rules, list):
        raise AccountError("workspace.json: accounts must be a list of rules")
    for i, r in enumerate(rules):
        if not isinstance(r, dict) or "match" not in r or set(r) - set(KEYS):
            raise AccountError(f"workspace.json: accounts[{i}] needs match and only {', '.join(KEYS)}")
        if any(not isinstance(v, str) or not v.strip("/").strip() for v in r.values()):
            raise AccountError(f"workspace.json: accounts[{i}] values must be non-empty strings")
    return rules


def rule_for(name: str) -> dict:
    """The first rule whose match is the repo `name` or a folder above it; {} when none."""
    for r in _rules():
        m = r["match"].strip("/")
        if name == m or name.startswith(m + "/"):
            return r
    return {}


def claude_env(name: str) -> dict:
    """{"CLAUDE_CONFIG_DIR": dir} for the repo's rule, {} for the personal account."""
    d = rule_for(name).get("claude_config_dir")
    if not d:
        return {}
    p = Path(d).expanduser()
    if not p.is_dir():
        raise AccountError(f"{name}: claude_config_dir {p} does not exist")
    return {"CLAUDE_CONFIG_DIR": str(p)}


def gh_env(name: str) -> dict:
    """{"GH_TOKEN": token} of the repo's gh_user, {} for gh's active account."""
    user = rule_for(name).get("gh_user")
    if not user:
        return {}
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    try:
        r = subprocess.run(["gh", "auth", "token", "--user", user], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, env=env, timeout=GH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AccountError(f"{name}: gh auth token --user {user}: {e}") from None
    token = r.stdout.strip()
    if r.returncode or not token:
        raise AccountError(f"{name}: gh has no token for {user}: {(r.stderr or r.stdout).strip()}")
    return {"GH_TOKEN": token}


def check(name: str) -> None:
    """Raise AccountError when the repo's rule cannot be used."""
    claude_env(name)
    gh_env(name)
