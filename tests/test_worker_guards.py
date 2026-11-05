import json
import os
import subprocess
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def hook(name, payload, **env_over):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARK_")}
    env.update(env_over)
    cmd = ["bash", str(CODE / "hooks" / name)] if name.endswith(".sh") else ["python3", str(CODE / "hooks" / name)]
    r = subprocess.run(cmd, input=payload if isinstance(payload, str) else json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def decision(out):
    return out["hookSpecificOutput"]["permissionDecision"] if out else "allow"


def worker_env(wt, report, **over):
    return {"ARK_ROLE": "worker", "ARK_WORKTREE": str(wt), "ARK_AREA": json.dumps(["src/shell/**"]),
            "ARK_REPORT": str(report), **over}


def test_area_guard(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    report = tmp_path / "runs" / "shell.report.md"
    env = worker_env(wt, report)
    w = lambda p, **e: decision(hook("area-guard.py", {"tool_name": "Write", "tool_input": {"file_path": p},
                                                       "cwd": str(wt)}, **{**env, **e}))
    assert w(str(wt / "src/shell/a.py")) == "allow"
    assert w("src/shell/b.py") == "allow"
    assert w(str(wt / "src/ui/a.py")) == "deny"
    assert w(str(wt / "src/shell/../ui/a.py")) == "deny"
    assert w("/tmp/elsewhere.py") == "deny"
    assert w(str(report)) == "allow"
    assert w(str(wt / "src/shell/a.py"), ARK_ROLE="reviewer") == "deny"
    assert w(str(wt / "src/ui/a.py"), ARK_ROLE="") == "allow"          # Marco's sessions: untouched
    assert decision(hook("area-guard.py", {"tool_name": "Read", "tool_input": {"file_path": "/x"}}, **env)) == "allow"


def test_area_guard_fails_closed_for_workers(tmp_path):
    env = worker_env(tmp_path, tmp_path / "r.md")
    for payload in ["", "not json", {"tool_name": "Edit", "tool_input": {}},
                    {"tool_name": "Edit", "tool_input": {"file_path": 5}}]:
        assert decision(hook("area-guard.py", payload, **env)) == "deny", payload
    assert decision(hook("area-guard.py", "not json")) == "allow"       # no role: never blocks


def test_area_guard_fails_closed_on_broken_environment(tmp_path):
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "src/shell/a.py")},
               "cwd": str(tmp_path)}
    good = worker_env(tmp_path, tmp_path / "r.md")
    assert decision(hook("area-guard.py", payload, **good)) == "allow"
    for bad in [{"ARK_WORKTREE": ""}, {"ARK_AREA": ""}, {"ARK_AREA": "not json"}, {"ARK_AREA": '"src/**"'},
                {"ARK_AREA": "[]"}, {"ARK_AREA": "[5]"}]:
        assert decision(hook("area-guard.py", payload, **{**good, **bad})) == "deny", bad
    unset = {k: v for k, v in good.items() if k != "ARK_AREA"}
    assert decision(hook("area-guard.py", payload, **unset)) == "deny"
    # an unknown role is not Marco: denied rather than ignored
    assert decision(hook("area-guard.py", payload, **{**good, "ARK_ROLE": "bogus"})) == "deny"


def test_area_guard_symlink_out_of_the_area(tmp_path):
    wt = tmp_path / "wt"
    (wt / "src/shell").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (wt / "src/shell/link").symlink_to(tmp_path / "outside")
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(wt / "src/shell/link/x.py")}, "cwd": str(wt)}
    assert decision(hook("area-guard.py", payload, **worker_env(wt, tmp_path / "r.md"))) == "deny"


def test_read_guard(tmp_path):
    home = tmp_path / "ark"
    env = {"ARK_ROLE": "worker", "ARK_HOME": str(home)}
    r = lambda tool, inp, **e: decision(hook("read-guard.py", {"tool_name": tool, "tool_input": inp,
                                                               "cwd": str(tmp_path)}, **{**env, **e}))
    assert r("Read", {"file_path": str(home / "desk/demo/x/plan.md")}) == "deny"
    assert r("Grep", {"pattern": "x", "path": str(home / "approvals")}) == "deny"
    assert r("Glob", {"pattern": "**/*.md", "path": str(home / "desk")}) == "deny"
    assert r("Bash", {"command": f"cat {home}/desk/demo/x/plan.md"}) == "deny"
    assert r("Bash", {"command": "cat ~/.claude/arkimedes/desk/demo/x/plan.md"}) == "deny"
    assert r("Read", {"file_path": str(home / "runs/demo/x/shell.report.md")}) == "allow"
    assert r("Read", {"file_path": str(tmp_path / "src/a.py")}) == "allow"
    assert r("Read", {"file_path": str(home / "desk/demo/x/plan.md")}, ARK_ROLE="") == "allow"
    assert r("Read", {"file_path": str(home / "desk/demo/x/plan.md")}, ARK_ROLE="tick") == "allow"


def test_read_guard_fails_closed_and_bash_forms(tmp_path):
    home = tmp_path / "ark"
    env = {"ARK_ROLE": "worker", "ARK_HOME": str(home)}
    for payload in ["", "not json", {"tool_name": "Bash", "tool_input": {}},
                    {"tool_name": "Read", "tool_input": {"file_path": 5}}]:
        assert decision(hook("read-guard.py", payload, **env)) == "deny", payload
    assert decision(hook("read-guard.py", "not json")) == "allow"
    for c in ["cat $HOME/.claude/arkimedes/desk/a/plan.md", "cat ~/.claude/arkimedes/approvals/x",
              "cd ~/.claude/arkimedes && cat desk/a/plan.md", "ls /Users/x/.claude/arkimedes/desk"]:
        out = hook("read-guard.py", {"tool_name": "Bash", "tool_input": {"command": c}}, **env)
        assert decision(out) == "deny", c
    # a symlink into the closed folders is resolved
    (home / "desk").mkdir(parents=True)
    (tmp_path / "link").symlink_to(home / "desk")
    out = hook("read-guard.py", {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "link/x")},
                                 "cwd": str(tmp_path)}, **env)
    assert decision(out) == "deny"


def test_merge_guard():
    m = lambda c, role="worker": decision(hook("merge-guard.py", {"tool_name": "Bash", "tool_input": {"command": c}},
                                               **({"ARK_ROLE": role} if role else {})))
    for c in ["gh pr merge 3 --squash", "gh pr review 3 --approve", "gh pr create --fill", "gh api repos/x/pulls",
              "git push origin HEAD", "git -C /wt push", "cd /wt && git push -f"]:
        assert m(c) == "deny", c
        assert m(c, role="tick") == "deny", c
    for c in ["git status", "gh pr view 3", "git commit -m 'push the button'", "git log --oneline"]:
        assert m(c) == "allow", c
    assert m("gh pr merge 3", role=None) == "allow"        # Marco's sessions: untouched


def test_merge_guard_option_forms_and_nesting():
    m = lambda c, role="worker": decision(hook("merge-guard.py", {"tool_name": "Bash", "tool_input": {"command": c}},
                                               ARK_ROLE=role))
    for c in ["git -c k=v push", "git -C '/a b' push", "git --git-dir /x push", "git --git-dir=/x push origin",
              "/usr/bin/git push", "command git push", "gh -R o/r pr merge 3", "gh --repo o/r pr create",
              "gh -R o/r api x", "bash -c 'git push'", "sh -c \"cd x && git push\"", "echo hi; git push",
              "echo $(git push)", "echo `git push`", "git 'push'", "eval 'gh pr merge 3'", "git commit -m 'unclosed"]:
        assert m(c) == "deny", c
    for c in ["git commit -m 'git push (later)'", "git -C /wt log", "gh pr view 3 --json state",
              "echo 'gh pr merge' > /dev/null"]:
        assert m(c) == "allow", c


def test_merge_guard_fails_closed():
    for payload in ["", "not json", {"tool_name": "Bash", "tool_input": {}},
                    {"tool_name": "Bash", "tool_input": {"command": 5}}]:
        assert decision(hook("merge-guard.py", payload, ARK_ROLE="worker")) == "deny", payload


def test_done_gate(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    rd = tmp_path / "run"
    rd.mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"text": "working"}\n')
    env = {"ARK_ROLE": "worker", "ARK_WORKTREE": str(wt), "ARK_RUN_DIR": str(rd), "ARK_JOB": "shell",
           "ARK_GATE": "test -f ok || (echo gate is red; exit 1)"}
    stop = lambda **e: hook("done-gate.sh", {"transcript_path": str(transcript)}, **{**env, **e})
    for n in range(3):
        out = stop()
        assert out["decision"] == "block" and "gate is red" in out["reason"]
    assert stop() is None                                   # 3 blocks: it may end FAILED
    (rd / "shell.doneblocks").unlink()
    transcript.write_text('{"text": "STATUS: FAILED — cannot"}\n')
    assert stop() is None                                   # it already says FAILED
    transcript.write_text('{"text": "done"}\n')
    (wt / "ok").write_text("")
    assert stop() is None                                   # green
    assert stop(ARK_ROLE="reviewer") is None


def test_done_gate_does_nothing_without_a_worker_environment(tmp_path):
    assert hook("done-gate.sh", {"transcript_path": ""}, ARK_ROLE="worker") is None
    assert hook("done-gate.sh", {"transcript_path": ""}) is None


def test_session_start_is_silent_for_runner_roles():
    r = subprocess.run(["bash", str(CODE / "hooks" / "session-start.sh")], input="{}", capture_output=True,
                       text=True, env={**os.environ, "ARK_ROLE": "worker"})
    assert r.returncode == 0 and r.stdout == ""
