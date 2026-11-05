import json
import os
import subprocess
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def guard(ark_home, tool, tool_input, *, role=None, cwd="/tmp"):
    env = {**os.environ, "ARK_HOME": str(ark_home)}
    env.pop("ARK_ROLE", None)
    if role:
        env["ARK_ROLE"] = role
    r = subprocess.run(["python3", str(CODE / "hooks/desk-guard.py")],
                       input=json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": cwd}),
                       capture_output=True, text=True, env=env, check=True)
    if not r.stdout.strip():
        return "allow"
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_no_tool_writes_approvals(ark_home):
    assert guard(ark_home, "Write", {"file_path": str(ark_home / "approvals/demo/x.json")}) == "deny"


def test_desk_is_writable_interactively_but_not_by_the_runner(ark_home):
    f = {"file_path": str(ark_home / "desk/demo/x/plan.md")}
    assert guard(ark_home, "Edit", f) == "allow"
    assert guard(ark_home, "Edit", f, role="worker") == "deny"


def test_shell_writes_to_approvals_are_denied_reads_allowed(ark_home):
    a = ark_home / "approvals/demo/x.json"
    assert guard(ark_home, "Bash", {"command": f"echo {{}} > {a}"}) == "deny"
    assert guard(ark_home, "Bash", {"command": f"rm {a}"}) == "deny"
    assert guard(ark_home, "Bash", {"command": f"cat {a} 2>/dev/null"}) == "allow"


def test_relative_paths_from_the_ark_folder(ark_home):
    assert guard(ark_home, "Bash", {"command": "touch approvals/demo/x.json"}, cwd=str(ark_home)) == "deny"
    assert guard(ark_home, "Bash", {"command": "ls approvals/"}, cwd=str(ark_home)) == "allow"


def test_runner_cannot_shell_write_the_desk(ark_home):
    cmd = {"command": f"sed -i '' s/draft/closed/ {ark_home}/desk/demo/x/plan.md"}
    assert guard(ark_home, "Bash", cmd) == "allow"
    assert guard(ark_home, "Bash", cmd, role="tick") == "deny"


def test_approving_only_through_ark_approve(ark_home):
    assert guard(ark_home, "Bash", {"command": ".venv/bin/python -m ark.cli approve demo/x"}) == "deny"
    assert guard(ark_home, "Bash", {"command": "ark approve demo/x"}) == "allow"


def test_unrelated_calls_pass(ark_home):
    assert guard(ark_home, "Bash", {"command": "rm -rf build"}) == "allow"
    assert guard(ark_home, "Write", {"file_path": "/tmp/x.md"}) == "allow"
