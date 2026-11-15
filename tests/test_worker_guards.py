import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from ark import claude, gitops

CODE = Path(__file__).resolve().parents[1]
SYSTEM_PYTHON = "/usr/bin/python3"   # 3.9 on macOS: what `python3` is with the default PATH


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


def test_area_guard_reads_live_grants(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    grants = tmp_path / "shell.grants.json"
    env = worker_env(wt, tmp_path / "r.md", ARK_GRANTS_FILE=str(grants), ARK_COMPANIONS="0")
    w = lambda p, **e: decision(hook("area-guard.py", {"tool_name": "Write", "tool_input": {"file_path": p},
                                                       "cwd": str(wt)}, **{**env, **e}))
    assert w("src/ui/a.py") == "deny"                       # no file: no grants
    grants.write_text(json.dumps(["src/ui/a.py"]))
    assert w("src/ui/a.py") == "allow" and w("src/ui/b.py") == "deny"
    grants.write_text(json.dumps(["src/ui/**"]))             # globs count as in run.area
    assert w("src/ui/b.py") == "allow" and w("src/other.py") == "deny"
    for bad in ["not json", "{}", '"src/ui/a.py"', "[5]", '["src/ui/a.py", ""]', "", "null"]:
        grants.write_text(bad)                                # malformed: no grants, never open
        assert w("src/ui/a.py") == "deny", bad
    grants.unlink()
    grants.mkdir()                                            # unreadable
    assert w("src/ui/a.py") == "deny"
    assert w("src/ui/a.py", ARK_GRANTS_FILE=str(tmp_path / "missing.json")) == "deny"
    assert w("src/shell/a.py") == "allow"                    # the area itself is unaffected


def test_area_guard_allows_companions_outside_the_area(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    env = worker_env(wt, tmp_path / "r.md")
    w = lambda p, **e: decision(hook("area-guard.py", {"tool_name": "Write", "tool_input": {"file_path": p},
                                                       "cwd": str(wt)}, **{**env, **e}))
    for p in ["tests/test_brief.py", "tests/conftest.py", "src/ui/a.test.ts", "docs/runner-control.md",
              "docs/decisions/x.md", "pkg/__tests__/a.ts"]:
        assert w(p) == "allow", p
    for p in ["lib/ark/loop.py", "src/ui/a.ts", "tsconfig.json", "package.json", "vitest.config.ts", "tests/../lib/a.py",
              ".git/hooks/pre-commit", ".claude/settings.json"]:
        assert w(p) == "deny", p
    assert w("tests/test_brief.py", ARK_COMPANIONS="0") == "deny"   # the plan opted out
    assert w("tests/test_brief.py", ARK_ROLE="reviewer") == "deny"


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


def test_merge_guard_denies_euklides_its_push_and_pr_commands():
    m = lambda c: decision(hook("merge-guard.py", {"tool_name": "Bash", "tool_input": {"command": c}},
                                ARK_ROLE="euklides"))
    for c in ["git push origin HEAD", "git push --force-with-lease origin ark/x", "gh pr merge 3 --squash",
              "gh pr create --fill", "gh api repos/x/pulls"]:
        assert m(c) == "deny", c
    for c in ["git status", "git reset --hard HEAD~1", "git log --oneline"]:
        assert m(c) == "allow", c


def test_merge_guard_option_forms_and_nesting():
    m = lambda c, role="worker": decision(hook("merge-guard.py", {"tool_name": "Bash", "tool_input": {"command": c}},
                                               ARK_ROLE=role))
    for c in ["git -c k=v push", "git -C '/a b' push", "git --git-dir /x push", "git --git-dir=/x push origin",
              "/usr/bin/git push", "command git push", "gh -R o/r pr merge 3", "gh --repo o/r pr create",
              "gh -R o/r api x", "bash -c 'git push'", "sh -c \"cd x && git push\"", "echo hi; git push",
              "echo $(git push)", "echo `git push`", "git 'push'", "eval 'gh pr merge 3'", "git commit -m 'unclosed push"]:
        assert m(c) == "deny", c
    for c in ["git commit -m 'git push (later)'", "git -C /wt log", "gh pr view 3 --json state",
              "echo 'gh pr merge' > /dev/null"]:
        assert m(c) == "allow", c


def test_merge_guard_fails_closed():
    for payload in ["", "not json", {"tool_name": "Bash", "tool_input": {}},
                    {"tool_name": "Bash", "tool_input": {"command": 5}}]:
        assert decision(hook("merge-guard.py", payload, ARK_ROLE="worker")) == "deny", payload


def assistant_line(text):
    return json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                        "content": [{"type": "text", "text": text}]}}) + "\n"


def test_done_gate(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    rd = tmp_path / "run"
    rd.mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(assistant_line("working"))
    env = {"ARK_ROLE": "worker", "ARK_WORKTREE": str(wt), "ARK_RUN_DIR": str(rd), "ARK_JOB": "shell",
           "ARK_GATE": "test -f ok || (echo gate is red; exit 1)"}
    stop = lambda **e: hook("done-gate.sh", {"transcript_path": str(transcript)}, **{**env, **e})
    for n in range(3):
        out = stop()
        assert out["decision"] == "block" and "gate is red" in out["reason"]
    assert stop() is None                                   # 3 blocks: it may end FAILED
    (rd / "shell.doneblocks").unlink()
    transcript.write_text(assistant_line("STATUS: FAILED — cannot"))
    assert stop() is None                                   # it already says FAILED
    transcript.write_text(assistant_line("done"))
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


P = "origin HEAD:main"


def merge_decisions(commands, role="worker", python="python3"):
    out = {}
    for c in commands:
        r = subprocess.run([python, str(CODE / "hooks/merge-guard.py")], capture_output=True, text=True,
                           input=json.dumps({"tool_name": "Bash", "tool_input": {"command": c}}),
                           env={**os.environ, "ARK_ROLE": role})
        assert r.returncode == 0, (c, r.stderr)
        out[c] = decision(json.loads(r.stdout) if r.stdout.strip() else None)
    return out


def assert_all(commands, expected):
    wrong = [c for c, d in merge_decisions(commands).items() if d != expected]
    assert not wrong, f"should be {expected}: {wrong}"


def test_merge_guard_review_table_is_denied():
    assert_all([
        # newlines
        f"true\ngit push {P}", "true\ngh pr merge 3", f"sh -c 'true\ngit push {P}'", f"bash <<E\ngit push {P}\nE",
        # '#' inside a word
        f"echo a#b; git push {P}", f"git log -1 --format=%h# ; git push {P}", "echo a#b; gh pr merge 3",
        # substitutions inside double quotes
        f'echo "$(git push {P})"', f'x="$(git push {P})"', f'echo "`git push {P}`"',
        f'git commit --allow-empty -m "$(git push {P})"', 'echo "$(gh pr merge 3)"',
        # reserved words
        f"{{ git push {P}; }}", f"if true; then git push {P}; fi", f"if git push {P}; then true; fi",
        f"for i in 1; do git push {P}; done", f"! git push {P}", f"f() {{ git push {P}; }}; f", "{ gh pr merge 3; }",
        # wrappers
        f"env -i PATH=$PATH git push {P}", f"env -u FOO git push {P}", f"env -S 'git push {P}'",
        f"command -p git push {P}", f"exec -a x git push {P}", f"time -p git push {P}", f"nice git push {P}",
        f"caffeinate -i git push {P}",
        # xargs
        "xargs -n2 git push", f"xargs -I{{}} git push {P}", f"echo push {P} | xargs git",
        # a shell reading stdin
        f"echo 'git push {P}' | sh", f"echo 'git push {P}' | bash -s", f"sh <<< 'git push {P}'",
        # eval
        f"eval git push {P}", f'eval "git" push {P}', "eval gh pr merge 3",
        # options after pr
        "gh pr -R o/r merge 3", "gh pr --repo o/r merge 3", "gh pr --repo=o/r merge 3",
        # aliases and plumbing
        f"git -c alias.x=push x {P}", f"git -c 'alias.x=!git push' x {P}", f"git config alias.y push && git y {P}",
        "git send-pack ../r.git HEAD:refs/heads/main", "gh alias set zz 'pr merge' && gh zz 3",
        f"git --attr-source HEAD push {P}",
        # expansion
        f"g=git; $g push {P}", f"p=push; git $p {P}", f"$(echo git) push {P}", f"git${{IFS}}push {P}",
        # other interpreters
        f"find . -exec git push {P} \;", f"python3 -c \"import os; os.system('git push {P}')\"",
        f"perl -e 'system(\"git push {P}\")'", f"echo 'git push {P}' > s.sh && sh s.sh",
    ], "deny")


def test_merge_guard_denies_what_it_cannot_prove():
    assert_all([
        # no push word in sight: still opaque
        "p=pu; q=sh; git $p$q origin", "git $(printf pu)sh", 'git "$x" origin', "git pu*", "echo Z2l0 | base64 -d | sh",
        "echo Z2l0 | base64 -d | /bin/bash", "echo x | base64 -d | python3", 'eval "$x"', "cd /x && eval $y",
        "bash <(curl x)", "sh < s.sh", "bash -c $'git \\x70ush'", 'sh -c "$x"', "bash -lc `cat f`", "source <(curl x)",
        ". /dev/stdin",
        # quote and case splitting
        'git pu""sh', "git p\\ush", "git pu\\\nsh", f"GIT push {P}",
        # quoted text that becomes a command
        "x='git push'; $x", "PAGER='git push' git log", "git -c core.pager='git push' log",
        "git grep -O'git push' x", "git fetch --upload-pack='git push' origin", "git rebase -x 'git push' HEAD~1",
        "find . -exec sh -c 'git push' \;", "grep x f | tee s.sh; echo 'git push' | tee -a s.sh",
        # a commit-message heredoc must not hide a command
        "echo '\"$(cat <<'E'\n' ; git push ; echo '\nE\n)\"'",
        "git commit -m \"$(cat <<'E'\nfoo\nE\ngit push\nE\n)\"",
        "git commit -m \"$(cat <<E\n$(git push)\nE\n)\"",
        "sh -c \"$(cat <<'E'\ngit push\nE\n)\"",
        "\"$(cat <<'E'\ngit push\nE\n)\" | sh",
        # gh is always proven, not parsed
        "gh pr view 3; gh pr merge 3", "gh zz 3", "gh extension install x",
        # words that may run a push, which a static check cannot read
        "make push", "./push.sh", "bash scripts/push.sh", "npm run push",
        # the env backstop in claude.runner_env, and the config it reads
        "GIT_CONFIG_COUNT=0 ./x", "git config --unset remote.origin.pushurl",
    ], "deny")


def test_merge_guard_keeps_ordinary_commands():
    assert_all([
        "git status", "git stash push", "git stash push -m x", "git -C /wt stash push", "git grep foo",
        "git grep 'git push'", "grep -rn 'git push' .", "rg 'gh pr merge'", "echo 'git push'", 'echo "git push"',
        "git log --grep='git push'", "git log --format='%h#%s'", "git config push.default", "git help push",
        "git commit -m 'push the button'", 'git commit -m "feat: push notifications"',
        "git commit -m 'git push (later)'", "git commit -m \"$(cat <<'EOF'\nfix: don't git push\n\nCo-Authored-By: x\nEOF\n)\"",
        "git add a && git commit -m \"$(cat <<'EOF'\nmerge it, then | sh\nEOF\n)\" 2>&1 | tail -5",
        "git commit -m \"$(cat <<EOF\nfix it's\nEOF\n)\"",
        "xargs grep 'git push'", "find . -name '*.py' | xargs grep 'git push'", "find . -name '*.py' -exec wc -l {} \;",
        "cat > f <<'EOF'\nit's fine\nEOF", "cat > f <<EOF\nit's fine\nEOF", "python3 - <<'PY'\nprint(\"it's\")\nPY",
        "gh pr view 3", "gh pr list", "gh pr checks 3", "gh pr view 3 --json title | jq .title", "gh -R o/r pr view 3",
        "gh pr -R o/r view 3", "sed -E 's/(a|b)/c/' f", "awk '$1 ~ /#/' f",
        "git merge origin/main", "git merge --no-edit origin/main 2>&1 | tail -20", "git add .\ngit commit -m 'push it'",
        "cd /wt && git status", 'git -C "$WT" status', "git -C '/a b' status", "git --no-pager log", "git log $x",
        "pushd x", "git fetch origin && git status", "bash scripts/test.sh", "sh -c 'make test'", "eval_results.sh", "bash -c 'echo $HOME'", "source .venv/bin/activate",
        'git commit -m "eval fix"', "pytest -k 'a and not b' && ruff check .", "curl -s x | python3 -m json.tool",
        # a path with the word in it is not a push
        "flutter test test/push/x_test.dart", ".venv/bin/python -m pytest tests/merge/ -q", "cat lib/push.py",
        "grep -rn merge lib | head -3", "ls .github", "cat lib/ark/merge.py",
    ], "allow")


def test_merge_guard_options_that_run_a_program():
    x = "git push origin HEAD:main"
    assert_all([
        # abbreviated long options, joined or as the next word
        f"git fetch --upload-pa='{x}; git-upload-pack .' .", f"git fetch --upload-pa '{x}; git-upload-pack .' .",
        f"git fetch --up='{x}' .", f"git grep --open-files-in-pa='{x} #' -e x", f"git diff --ext-dif merge",
        f"sort --compress-prog='{x}' -S1 /etc/hosts", f"sort --compress-prog '{x}' -S1 /etc/hosts",
        f"xargs sort --compress-prog='{x}'",
        # bundled short options, also behind one that looks like it takes text
        f"git grep -nO'{x} #' -e x", f"git grep -GO'{x} #' -e x", f"git grep -iS'{x}' -e x",
        # readers with a pager
        f"ack --pager='{x}' y", f"ack --pag='{x}' y", f"ag --pager '{x}' y",
        # any option holding the word, unless it is known to hold text
        f"git log --foo='{x}'", f"grep --foo='{x}' f",
    ], "deny")
    assert_all([
        f"git log --grep='{x}'", f"git log --format='{x}'", f"git log --pretty=format:'merge %h'",
        f"git commit -m'{x}'", f"git commit -m '{x}'", f"git commit -am '{x}'", f"git commit --message='{x}'",
        f"grep -e '{x}' f", f"grep -e'{x}' f", f"grep --regexp='{x}' f", "grep -rn --include='*push*' x .",
        "git log -- push", "git log --all -- lib/merge", "sort - push", "git log --oneline --all merge",
    ], "allow")


def test_merge_guard_spelling_does_not_hide_a_shape_it_always_denies():
    x = "origin HEAD:main"
    assert_all([
        f'p=pu; "git" ${{p}}sh {x}', f"p=pu; g\\it ${{p}}sh {x}", f"\"git\" $'\\x70ush' {x}",
        f"echo $'git \\x70ush {x}' | s''h", f"\\eval $'git \\x70ush {x}'", f"e''val $'git \\x70ush {x}'",
        f"git pu{{sh,}} {x}", f"git {{pu,}}sh {x}",
    ], "deny")


def test_merge_guard_filenames_are_not_mentions():
    assert_all([
        "dart test test/push.dart", "flutter analyze lib/push.dart", "touch lib/features/push.dart",
        "mv lib/push.dart lib/notif.dart", "rm lib/merge.py", "sed -n 1,40p lib/push.dart", "python3 lib/merge.py",
        "node scripts/merge.js", "git config push.default simple",
    ], "allow")
    # a shell script named after it is still a thing that may push, and a real push is still a push
    assert_all(["./push.sh", "bash scripts/push.sh", "sh merge.sh", "git push", "git push. x", "git push .",
                "git push origin ark/x.y"], "deny")


def test_merge_guard_config_that_breaks_or_lifts_the_runners_push():
    assert_all([
        "git config remote.origin.url /x", "git config remote.origin.mirror true", "git remote set-url origin /x",
        "git config url./x.insteadOf /dev/null/no-push-from-workers", "git config url./x.pushInsteadOf y",
    ], "deny")
    assert_all(["git config remote.origin.url", "git config --get remote.origin.url"], "allow")


def test_merge_guard_always_deny_reason_is_not_a_stop_signal():
    r = subprocess.run(["python3", str(CODE / "hooks/merge-guard.py")], capture_output=True, text=True,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": 'eval "$(direnv export bash)"'}}),
                       env={**os.environ, "ARK_ROLE": "worker"})
    reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "not a stop signal" in reason and "Commit and finish" not in reason


def test_merge_guard_stays_quick_on_a_huge_mention():
    start = time.monotonic()
    assert_all(["gh " + "pr " * 33000], "deny")
    assert time.monotonic() - start < 5


def test_merge_guard_reason_says_how_to_retry():
    r = subprocess.run(["python3", str(CODE / "hooks/merge-guard.py")], capture_output=True, text=True,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest -k push"}}),
                       env={**os.environ, "ARK_ROLE": "worker"})
    reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Write tool" in reason and "not a stop signal" in reason


@pytest.mark.parametrize("name", ["merge-guard.py", "area-guard.py", "read-guard.py"])
def test_guards_deny_a_missing_tool_name(name, tmp_path):
    env = {**worker_env(tmp_path, tmp_path / "r.md"), "ARK_HOME": str(tmp_path)}
    for payload in [{}, {"tool_input": {"command": "git push"}}, {"tool_name": None}, {"tool_name": 5}]:
        assert decision(hook(name, payload, **env)) == "deny", payload


@pytest.mark.skipif(not Path(SYSTEM_PYTHON).exists(), reason="no system python")
def test_guards_work_on_the_system_python(tmp_path):
    assert merge_decisions([f"git push {P}", "git commit -m 'push'", "true\ngh pr merge 3"], python=SYSTEM_PYTHON) == {
        f"git push {P}": "deny", "git commit -m 'push'": "allow", "true\ngh pr merge 3": "deny"}
    env = {**os.environ, **worker_env(tmp_path, tmp_path / "r.md"), "ARK_HOME": str(tmp_path / "ark")}
    def run(name, payload):
        r = subprocess.run([SYSTEM_PYTHON, str(CODE / "hooks" / name)], input=json.dumps(payload), capture_output=True,
                           text=True, env=env)
        assert r.returncode == 0, r.stderr
        return decision(json.loads(r.stdout) if r.stdout.strip() else None)
    write = lambda p: {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / p)}, "cwd": str(tmp_path)}
    assert run("area-guard.py", write("src/shell/a.py")) == "allow"
    assert run("area-guard.py", write("src/ui/a.py")) == "deny"
    read = lambda p: {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / p)}, "cwd": str(tmp_path)}
    assert run("read-guard.py", read("src/a.py")) == "allow"
    assert run("read-guard.py", read("ark/desk/a/plan.md")) == "deny"


def settings():
    return json.loads((CODE / "worker-settings.json").read_text())


def pretool_hooks():
    return [h for g in settings()["hooks"]["PreToolUse"] for h in g["hooks"]]


def run_form(command, script, payload, path=None):
    """A hook's command exactly as the settings write it, pointed at `script`."""
    for name in ("area-guard.py", "read-guard.py", "merge-guard.py"):
        command = command.replace(f"/Users/marcozanger/.claude/arkimedes/hooks/{name}", str(script))
    env = {**os.environ, "ARK_ROLE": "worker"}
    if path is not None:
        env["PATH"] = str(path)
    return subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload), capture_output=True, text=True, env=env)


def test_a_failing_guard_blocks_in_the_settings_form(tmp_path):
    crash = tmp_path / "crash.py"
    crash.write_text("raise TypeError('boom')\n")
    empty = tmp_path / "empty"
    empty.mkdir()
    payload = {"tool_name": "Bash", "tool_input": {"command": "git push"}}
    hooks = pretool_hooks()
    assert len(hooks) == 3
    for h in hooks:
        assert h["onFailure"] == "block", h
        assert run_form(h["command"], crash, payload).returncode == 2, h      # crash
        assert run_form(h["command"], crash, payload, path=empty).returncode == 2, h   # no python3 on PATH
        assert run_form(h["command"], tmp_path / "gone.py", payload).returncode == 2, h


def test_the_settings_form_keeps_a_json_deny_and_an_allow():
    h = next(h for h in pretool_hooks() if "merge-guard" in h["command"])
    guard = CODE / "hooks/merge-guard.py"
    denied = run_form(h["command"], guard, {"tool_name": "Bash", "tool_input": {"command": "git push"}})
    assert denied.returncode == 0 and decision(json.loads(denied.stdout)) == "deny"
    allowed = run_form(h["command"], guard, {"tool_name": "Bash", "tool_input": {"command": "git status"}})
    assert allowed.returncode == 0 and allowed.stdout == ""


def test_worker_settings_close_the_side_doors():
    s = settings()
    assert s["disableAllHooks"] is False
    assert "Monitor" in s["permissions"]["deny"]
    stop = s["hooks"]["Stop"][0]["hooks"][0]
    assert stop["timeout"] >= 7200      # a gate may run longer than the default 600 s


def test_push_layer_stops_the_jobs_origin_and_nothing_else(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    def git(*a, cwd=tmp_path, e=env):
        return subprocess.run(["git", *a], cwd=cwd, env=e, capture_output=True, text=True)
    git("init", "-q", "--bare", "-b", "main", "r.git")
    git("clone", "-q", "r.git", "c")
    clone = tmp_path / "c"
    url = str(tmp_path / "r.git")
    git("commit", "-q", "--allow-empty", "-m", "0", cwd=clone)
    assert git("push", "-q", "origin", "HEAD:main", cwd=clone).returncode == 0     # without the layer it works
    git("commit", "-q", "--allow-empty", "-m", "1", cwd=clone)
    git("remote", "add", "same", url, cwd=clone)
    head = lambda: git("rev-parse", "main", cwd=tmp_path / "r.git").stdout
    before = head()
    worker = {**env, **claude.runner_env("worker", repo_name=None, origin_url=gitops.origin_url(clone))}
    for args in (["push", "origin", "HEAD:main"], ["-c", "alias.x=push", "x", "origin", "HEAD:main"],
                 ["push", url, "HEAD:main"], ["push", "same", "HEAD:main"]):
        assert git(*args, cwd=clone, e=worker).returncode != 0, args
    assert head() == before
    assert git("fetch", "origin", cwd=clone, e=worker).returncode == 0
    # a repo the code under test makes for itself: its gate must stay green
    git("init", "-q", "--bare", "-b", "main", "fixture.git")
    git("clone", "-q", "fixture.git", "fc")
    git("commit", "-q", "--allow-empty", "-m", "0", cwd=tmp_path / "fc")
    assert git("push", "-q", "origin", "HEAD:main", cwd=tmp_path / "fc", e=worker).returncode == 0


@pytest.mark.parametrize("url", ["git@github.com:o/r.git", "https://github.com/o/r.git", "ssh://git@github.com/o/r"])
def test_push_layer_matches_how_origin_is_spelled(url, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", url], check=True)
    # no network: this prints where a push would go
    push_url = lambda e: subprocess.run(["git", "-C", str(tmp_path), "remote", "get-url", "--push", "origin"], env=e,
                                        capture_output=True, text=True).stdout.strip()
    assert push_url(os.environ) == url
    worker = claude.runner_env("worker", repo_name=None, origin_url=gitops.origin_url(tmp_path))
    assert push_url(worker) == "/dev/null/no-push-from-workers"


def test_origin_url_is_the_spelling_the_layer_needs(tmp_path):
    """`git remote get-url` applies insteadOf, and pushInsteadOf matches the configured text."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/o/r.git"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "url.git@github.com:.insteadOf", "https://github.com/"],
                   check=True)
    assert gitops.origin_url(tmp_path) == "https://github.com/o/r.git"
    worker = claude.runner_env("worker", repo_name=None, origin_url=gitops.origin_url(tmp_path))
    r = subprocess.run(["git", "-C", str(tmp_path), "remote", "get-url", "--push", "origin"], env=worker,
                       capture_output=True, text=True)
    assert r.stdout.strip() == "/dev/null/no-push-from-workers"


def test_worker_settings_do_not_set_the_layer():
    assert not [k for k in settings()["env"] if k.startswith("GIT_CONFIG_")]


def done_gate_env(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    rd = tmp_path / "run"
    rd.mkdir()
    return {"ARK_ROLE": "worker", "ARK_WORKTREE": str(wt), "ARK_RUN_DIR": str(rd), "ARK_JOB": "shell",
            "ARK_GATE": "echo gate is red; exit 1"}


BRIEF = "End with exactly one of:\nSTATUS: DONE\nSTATUS: FAILED — <one line: why>\nSTATUS: BLOCKED — <question>\n"


def test_done_gate_ignores_status_text_earlier_in_the_session(tmp_path):
    env = done_gate_env(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": BRIEF}}) + "\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "denied, end with STATUS: BLOCKED."}]}}) + "\n"
        + assistant_line("STATUS: FAILED — an old answer, then I went on")
        + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "All done."}, {"type": "tool_use", "name": "Bash", "input": {}}]}}) + "\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "or end with STATUS: FAILED — <why>."}]}}) + "\n")
    out = hook("done-gate.sh", {"transcript_path": str(transcript)}, **env)
    assert out["decision"] == "block"


def test_done_gate_wants_the_status_as_the_last_line(tmp_path):
    env = done_gate_env(tmp_path)
    for text in ["I would say STATUS: FAILED — maybe, but no", "STATUS: FAILED — x\nactually, more text",
                 " STATUS: BLOCKED — indented", "STATUS: FAILEDISH", "STATUS: DONE"]:
        out = hook("done-gate.sh", {"last_assistant_message": text}, **env)
        assert out["decision"] == "block", text
        (tmp_path / "run/shell.doneblocks").unlink()
    for text in ["STATUS: FAILED — cannot", "report written.\n\nSTATUS: BLOCKED — which?\n\n", "STATUS:FAILED"]:
        assert hook("done-gate.sh", {"last_assistant_message": text}, **env) is None, text


def test_done_gate_prefers_the_stop_input_over_the_transcript(tmp_path):
    env = done_gate_env(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(assistant_line("STATUS: FAILED — lagging entry"))     # the transcript may lag
    out = hook("done-gate.sh", {"transcript_path": str(transcript), "last_assistant_message": "All good"}, **env)
    assert out["decision"] == "block"


def test_done_gate_reads_a_broken_transcript_as_no_status(tmp_path):
    env = done_gate_env(tmp_path)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("not json\n5\n" + assistant_line("STATUS: BLOCKED — which?") + "{broken")
    assert hook("done-gate.sh", {"transcript_path": str(transcript)}, **env) is None
    transcript.write_text("not json\n")
    assert hook("done-gate.sh", {"transcript_path": str(transcript)}, **env)["decision"] == "block"


def test_area_guard_always_protects_git_and_claude_settings(tmp_path):
    env = worker_env(tmp_path, tmp_path / "r.md", ARK_AREA=json.dumps(["**", "*", "**/*.json"]))
    w = lambda p, **ti: decision(hook("area-guard.py", {"tool_name": "Write", "tool_input": {"file_path": p, **ti},
                                                        "cwd": str(tmp_path)}, **env))
    for p in [".git", ".git/config", ".git/hooks/pre-push", ".GIT/config", ".claude/settings.json",
              ".claude/settings.local.json", "sub/.git/config"]:
        assert w(p) == "deny", p
    for p in ["a.py", ".gitignore", ".claude/agents/x.md", "src/settings.json"]:
        assert w(p) == "allow", p


def test_area_guard_checks_every_path_in_the_call(tmp_path):
    env = worker_env(tmp_path, tmp_path / "r.md")
    ti = {"file_path": str(tmp_path / "src/shell/a.py"), "notebook_path": str(tmp_path / "src/ui/n.ipynb")}
    assert decision(hook("area-guard.py", {"tool_name": "NotebookEdit", "tool_input": ti, "cwd": str(tmp_path)}, **env)) == "deny"
    ti = {"notebook_path": str(tmp_path / "src/shell/n.ipynb")}
    assert decision(hook("area-guard.py", {"tool_name": "NotebookEdit", "tool_input": ti, "cwd": str(tmp_path)}, **env)) == "allow"


def test_read_guard_denies_searching_a_parent_of_the_closed_folders(tmp_path):
    home = tmp_path / "ark"
    wt = tmp_path / "wt"
    wt.mkdir()
    env = {"ARK_ROLE": "worker", "ARK_HOME": str(home)}
    r = lambda tool, inp, cwd=wt: decision(hook("read-guard.py", {"tool_name": tool, "tool_input": inp, "cwd": str(cwd)},
                                                **env))
    assert r("Grep", {"pattern": "x", "path": str(home)}) == "deny"
    assert r("Grep", {"pattern": "x", "path": str(tmp_path)}) == "deny"
    assert r("Grep", {"pattern": "x"}, cwd=home) == "deny"
    assert r("Glob", {"pattern": f"{home}/**/*.md"}) == "deny"
    assert r("Glob", {"pattern": "../ark/desk/**"}) == "deny"
    assert r("Glob", {"pattern": "../**/plan.md"}) == "deny"
    assert r("Grep", {"pattern": "x", "path": str(wt), "glob": "../ark/desk/**"}) == "deny"
    assert r("Grep", {"pattern": "x", "path": str(wt), "glob": "*.py"}) == "allow"
    assert r("Grep", {"pattern": "x"}) == "allow"
    assert r("Glob", {"pattern": "**/*.py"}) == "allow"
    assert r("Glob", {"pattern": "src/*.py", "path": str(wt)}) == "allow"
    assert r("Read", {"file_path": str(home / "runs/x.md")}) == "allow"


def test_read_guard_denies_a_brace_that_climbs_out(tmp_path):
    home = tmp_path / "ark"
    wt = tmp_path / "wt"
    wt.mkdir()
    env = {"ARK_ROLE": "worker", "ARK_HOME": str(home)}
    r = lambda inp: decision(hook("read-guard.py", {"tool_name": "Glob", "tool_input": inp, "cwd": str(wt)}, **env))
    assert r({"pattern": "{../ark/desk,src}/**"}) == "deny"
    assert r({"pattern": "src/{a,../../ark/desk}/**"}) == "deny"
    assert r({"pattern": f"{{src,{home}/desk}}/**"}) == "deny"
    assert r({"pattern": "{src,lib}/**/*.py"}) == "allow"
    assert r({"pattern": "*.{py,md}"}) == "allow"
