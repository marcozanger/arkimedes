import json
from pathlib import Path

import pytest

from helpers import set_accounts

from ark import accounts, claude

CODE = Path(__file__).resolve().parents[1]


def test_worker_command(monkeypatch):
    monkeypatch.setenv("ARK_CLAUDE", "/x/claude")
    cmd = claude.agent_cmd("worker", "sonnet", "do it", session="u1", usd=2)
    assert cmd[:3] == ["/x/claude", "-p", "do it"]
    for pair in (["--model", "sonnet"], ["--session-id", "u1"], ["--max-budget-usd", "2"],
                 ["--output-format", "stream-json"], ["--permission-mode", "auto"]):
        i = cmd.index(pair[0])
        assert cmd[i:i + 2] == pair
    assert "--verbose" in cmd
    assert cmd[cmd.index("--settings") + 1] == str(CODE / "worker-settings.json")
    assert cmd[cmd.index("--append-system-prompt-file") + 1] == str(CODE / "agents/ark-dev.md")


def test_resume_and_tick_commands(monkeypatch):
    monkeypatch.setenv("ARK_CLAUDE", "/x/claude")
    r = claude.agent_cmd("worker", "opus", "fix", session="u1", resume=True, usd=3)
    assert r[r.index("--resume") + 1] == "u1" and "--session-id" not in r
    t = claude.agent_cmd("tick", "opus", "judge", session="u2", usd=1)
    assert t[t.index("--output-format") + 1] == "json"
    assert t[t.index("--disallowedTools") + 1] == "Bash,Write,Edit,MultiEdit,NotebookEdit"
    assert "--append-system-prompt-file" not in t
    v = claude.agent_cmd("reviewer", "sonnet", "review", session="u3", usd=1)
    assert v[v.index("--append-system-prompt-file") + 1].endswith("agents/ark-reviewer.md")


def test_runner_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("CLAUDECODE", "1")
    env = claude.runner_env("worker", repo_name=None, origin_url="u", area='["src/**"]', job="shell")
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env
    assert env["ARK_ROLE"] == "worker" and env["ARKIMEDES"] == "1"
    assert env["ARK_AREA"] == '["src/**"]' and env["ARK_JOB"] == "shell"


def test_runner_env_scopes_the_push_layer_to_the_origin(monkeypatch):
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    env = claude.runner_env("worker", repo_name=None, origin_url="git@github.com:o/r.git")
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "url./dev/null/no-push-from-workers.pushInsteadOf"
    assert env["GIT_CONFIG_VALUE_0"] == "git@github.com:o/r.git"
    assert "ARK_ORIGIN_URL" not in env
    assert "GIT_CONFIG_COUNT" not in claude.os.environ      # the loop's own git stays unlayered


def test_runner_env_keeps_git_config_it_inherits(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.sshCommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "ssh -q")
    env = claude.runner_env("reviewer", repo_name=None, origin_url="u")
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "core.sshCommand"
    assert env["GIT_CONFIG_KEY_1"] == "url./dev/null/no-push-from-workers.pushInsteadOf"
    assert env["GIT_CONFIG_VALUE_1"] == "u"


@pytest.mark.parametrize("role", ["worker", "reviewer"])
@pytest.mark.parametrize("origin", [None, ""])
def test_runner_env_refuses_a_role_with_bash_and_no_origin(role, origin):
    with pytest.raises(ValueError):      # silently without the layer would be the worst outcome
        claude.runner_env(role, repo_name=None, origin_url=origin)


def test_runner_env_tick_needs_no_origin(monkeypatch):
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    assert "GIT_CONFIG_COUNT" not in claude.runner_env("tick", repo_name=None)


def test_tiers():
    assert [claude.MODELS[t] for t in ("T0", "T1", "T2")] == ["haiku", "sonnet", "opus"]
    assert claude.NEXT_TIER == {"T0": "T1", "T1": "T2"}
    assert claude.TIER_MINUTES == {"T0": 20, "T1": 60, "T2": 120}


def test_worker_settings_register_the_guards():
    s = json.loads((CODE / "worker-settings.json").read_text())
    commands = [h["command"] for groups in s["hooks"].values() for g in groups for h in g["hooks"]]
    for name in ("area-guard.py", "read-guard.py", "merge-guard.py", "done-gate.sh"):
        assert any(f"/hooks/{name}" in c for c in commands), name
    assert "Bash(git push:*)" in s["permissions"]["deny"]
    assert s["env"]["ARKIMEDES"] == "1"


def test_runner_env_uses_the_repo_account(ark_home, tmp_path):
    work = tmp_path / "claude-work"
    work.mkdir()
    set_accounts(ark_home, [{"match": "gl/", "claude_config_dir": str(work)}])
    assert claude.runner_env("tick", repo_name="gl/v0")["CLAUDE_CONFIG_DIR"] == str(work)
    assert claude.runner_env("worker", repo_name="gl/v0", origin_url="u")["CLAUDE_CONFIG_DIR"] == str(work)


def test_runner_env_drops_an_inherited_config_dir(ark_home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/elsewhere")
    assert "CLAUDE_CONFIG_DIR" not in claude.runner_env("tick", repo_name="demo")
    assert "CLAUDE_CONFIG_DIR" not in claude.runner_env("tick", repo_name=None)


def test_runner_env_never_falls_back(ark_home, tmp_path):
    set_accounts(ark_home, [{"match": "gl/", "claude_config_dir": str(tmp_path / "missing")}])
    with pytest.raises(accounts.AccountError):
        claude.runner_env("tick", repo_name="gl/v0")


def test_every_runner_env_call_names_the_repo():
    import re
    calls = [(f.name, m.group(0)) for f in sorted((CODE / "lib/ark").glob("*.py"))
             for m in re.finditer(r"runner_env\((?:[^()]|\([^()]*\))*\)", f.read_text())]
    assert len(calls) >= 9   # the definition and the eight calls in loop.py
    assert [c for c in calls if "repo_name" not in c[1]] == []
