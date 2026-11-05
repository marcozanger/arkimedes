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


def ak(tmp_path):
    h = tmp_path / "arkimedes"
    h.mkdir()
    return h


def test_approve_only_in_the_canonical_form(ark_home):
    deny_cmds = [
        "bin/ark approve demo/x",
        '"$HOME/.local/bin/ark" approve demo/x',
        "env ark approve demo/x",
        "command ark approve demo/x",
        "bash -c 'ark approve demo/x'",
        "echo demo/x | xargs ark approve",
        "yes s | script -q /dev/null ~/.local/bin/ark approve demo/x",
        "ark approve demo/x; echo done",
        """PYTHONPATH=lib .venv/bin/python -c 'from ark import cli; cli.main(["approve","demo/x"])'""",
    ]
    for c in deny_cmds:
        assert guard(ark_home, "Bash", {"command": c}) == "deny", c
    for c in ["ark approve demo/x", "~/.local/bin/ark approve demo/x", "/a/b/bin/ark approve demo/x"]:
        assert guard(ark_home, "Bash", {"command": c}) == "allow", c
    assert guard(ark_home, "Bash", {"command": "sed -n 75,120p lib/ark/cli.py | grep approve"}) == "allow"


def test_runner_never_approves_even_canonically(ark_home):
    for c in ["ark approve demo/x", "ARK_ROLE= ark approve demo/x",
              "env -u ARK_ROLE ~/.local/bin/ark approve demo/x"]:
        assert guard(ark_home, "Bash", {"command": c}, role="tick") == "deny", c


def test_shell_writes_after_cd_or_from_inside_the_folder(ark_home, tmp_path):
    h = ak(tmp_path)
    for c in [f"cd {h} && touch approvals/demo/x.json",
              f"cd {h} && echo {{}} > approvals/demo/x.json",
              f"D={h}; echo {{}} > $D/approvals/demo/x.json"]:
        assert guard(h, "Bash", {"command": c}) == "deny", c
    (h / "desk/demo").mkdir(parents=True)
    assert guard(h, "Bash", {"command": "touch ../../approvals/demo/x.json"}, cwd=str(h / "desk/demo")) == "deny"
    assert guard(h, "Bash", {"command": f"cd {h} && touch desk/demo/x/plan.md"}, role="tick") == "deny"


def test_more_runner_write_verbs(ark_home):
    d = ark_home / "desk/demo/x"
    for c in [f"git -C {ark_home}/desk checkout -- .",
              f"rsync -a /tmp/p/ {d}/",
              f"chmod 000 {d}/plan.md",
              f"bash -c \"touch {ark_home}/desk/x\""]:
        assert guard(ark_home, "Bash", {"command": c}, role="tick") == "deny", c


def test_discussing_the_folders_is_not_a_write(ark_home):
    cwd = str(ark_home)
    allowed = [
        "cat > /tmp/report.md <<'EOF'\nnotes about approvals and rm dead code\nEOF",
        'git commit -m "guard approvals -> only the CLI"',
        "git commit -m 'drop stale approvals handling, rm dead code'",
        "ls approvals/",
    ]
    for c in allowed:
        assert guard(ark_home, "Bash", {"command": c}, cwd=cwd) == "allow", c
    # a heredoc fed to an interpreter is code and stays checked
    assert guard(ark_home, "Bash", {"command": "bash <<'EOF'\ntouch approvals/x\nEOF"}, cwd=cwd) == "deny"
    assert guard(ark_home, "Bash", {"command": 'echo {} > "approvals/x"'}, cwd=cwd) == "deny"


def test_bad_input_never_crashes_or_blocks(ark_home):
    env = {**os.environ, "ARK_HOME": str(ark_home)}
    for payload in ["", "not json", "[]", "null", '{"tool_name":"Bash"}', '{"tool_name":"Bash","tool_input":"x"}',
                    '{"tool_name":"Write","tool_input":{"file_path":5}}',
                    '{"tool_name":"Bash","tool_input":{"command":["a"]},"cwd":3}',
                    '{"tool_name":"Write","tool_input":{"file_path":"a\\u0000b"}}']:
        r = subprocess.run(["python3", str(CODE / "hooks/desk-guard.py")], input=payload,
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0 and r.stdout.strip() == "", payload
