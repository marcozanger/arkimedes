import json
import subprocess
import time

import pytest

from ark import forge, gitops


def branch_with_file(repo, tmp_path, branch, base, name):
    wt = tmp_path / branch.replace("/", "-")
    gitops.add_worktree(repo, wt, branch, base)
    (wt / name).write_text("x\n")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", name], check=True)
    gitops.push_branch(wt, branch)
    return gitops.rev(wt, "HEAD")


def test_get_picks_local_only_when_asked(runner, monkeypatch):
    assert isinstance(forge.get(), forge.LocalForge)
    monkeypatch.delenv("ARK_FORGE")
    assert type(forge.get()) is forge.GhForge
    monkeypatch.setenv("ARK_FORGE", "gh")
    assert type(forge.get()) is forge.GhForge


def test_local_create_view_merge(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, base, "ark/x")
    head = branch_with_file(repo, tmp_path, "ark/x-job", base, "a.txt")
    f = forge.LocalForge()
    pr = f.create(repo, base="ark/x", head="ark/x-job", title="feat: a", body="b")
    v = f.view(repo, pr["number"])
    assert (v["base"], v["head"], v["head_sha"], v["state"]) == ("ark/x", "ark/x-job", head, "OPEN")
    with pytest.raises(forge.ForgeError):
        f.merge(repo, pr["number"], subject="feat: a", head_sha=base)   # head moved → refused
    f.merge(repo, pr["number"], subject="feat: a", head_sha=head)
    gitops.fetch(repo)
    assert gitops.changed_files(repo, base, "origin/ark/x") == ["a.txt"]
    assert f.view(repo, pr["number"])["state"] == "MERGED"
    assert [p["number"] for p in f.list(repo)] == [pr["number"]]


FAKE_GH = """#!/bin/sh
echo "$@" >> "$FAKE_DIR/argv"
echo "GH_PROMPT_DISABLED=$GH_PROMPT_DISABLED" >> "$FAKE_DIR/env"
[ "$1 $2" = "pr create" ] && cat > "$FAKE_DIR/stdin" || cat > /dev/null
mode=$(cat "$FAKE_DIR/mode_$1_$2" 2>/dev/null || echo ok)
case "$mode" in
  sleep) sleep 30 ;;
  stdin) cat ;;
  fail) echo "boom" >&2; exit 1 ;;
  raw) cat "$FAKE_DIR/out_$1_$2" ;;
  ok) cat "$FAKE_DIR/out_$1_$2" ;;
esac
"""
VIEW = {"baseRefName": "ark/x", "headRefName": "ark/x-j", "headRefOid": "a" * 40, "state": "OPEN", "url": "u"}


@pytest.fixture
def gh(tmp_path, monkeypatch):
    d = tmp_path / "fakegh"
    (d / "bin").mkdir(parents=True)
    exe = d / "bin" / "gh"
    exe.write_text(FAKE_GH)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{d / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_DIR", str(d))
    monkeypatch.setattr(forge, "GH_TIMEOUT", 2)

    class H:
        dir = d

        def out(self, cmd, text, mode="ok"):
            (d / f"out_pr_{cmd}").write_text(text)
            (d / f"mode_pr_{cmd}").write_text(mode)

        def view(self, **kw):
            self.out("view", json.dumps({**VIEW, **kw}))

        def argv(self):
            return (d / "argv").read_text().splitlines()
    h = H()
    h.out("create", "https://github.com/m/r/pull/7\n")
    h.view()
    h.out("merge", "")
    return h


def test_gh_create_view_argv_and_stdin(gh, tmp_path):
    g = forge.GhForge()
    assert g.create(tmp_path, base="ark/x", head="ark/x-j", title="t", body="the body") == \
        {"number": 7, "url": "https://github.com/m/r/pull/7"}
    assert gh.argv()[0] == "pr create --base ark/x --head ark/x-j --title t --body-file -"
    assert (gh.dir / "stdin").read_text() == "the body"
    assert "GH_PROMPT_DISABLED=1" in (gh.dir / "env").read_text()
    assert g.view(tmp_path, 7) == {"base": "ark/x", "head": "ark/x-j", "head_sha": "a" * 40,
                                   "state": "OPEN", "url": "u"}
    assert gh.argv()[-1] == "pr view 7 --json baseRefName,headRefName,headRefOid,state,url"


def test_gh_create_rejects_wrong_pr(gh, tmp_path):
    g = forge.GhForge()
    for text in ["", "no slash here", "<html>", "https://github.com/o/r/issues/7",
                 "https://github.com/o/r/pull/7/files", "https://github.com/o/r/pull/x"]:
        gh.out("create", text)
        with pytest.raises(forge.ForgeError):
            g.create(tmp_path, base="ark/x", head="ark/x-j", title="t", body="b")
    gh.out("create", "https://github.com/m/r/pull/7\n")
    gh.view(headRefName="someone-else")   # the number is another PR
    with pytest.raises(forge.ForgeError):
        g.create(tmp_path, base="ark/x", head="ark/x-j", title="t", body="b")


def test_gh_create_allows_default_branch_base(gh, tmp_path):
    gh.view(baseRefName="master", headRefName="ark/x")
    pr = forge.GhForge().create(tmp_path, base="master", head="ark/x", title="t", body="b")
    assert pr["number"] == 7   # the final PR is opened, never merged


def test_gh_merge_argv(gh, tmp_path):
    forge.GhForge().merge(tmp_path, 7, subject="t", head_sha="a" * 40)
    assert gh.argv()[-1] == f"pr merge 7 --squash --subject t --body  --match-head-commit {'a' * 40}"


@pytest.mark.parametrize("view,sha", [
    ({"baseRefName": "master"}, "a" * 40),
    ({"baseRefName": "main"}, "a" * 40),
    ({"baseRefName": "ark/../master"}, "a" * 40),
    ({"state": "MERGED"}, "a" * 40),
    ({"state": "CLOSED"}, "a" * 40),
    ({}, "b" * 40),
    ({}, ""),
    ({}, None),
    ({}, "abc"),
])
def test_gh_merge_refuses(gh, tmp_path, view, sha):
    gh.view(**view)
    with pytest.raises(forge.ForgeError):
        forge.GhForge().merge(tmp_path, 7, subject="t", head_sha=sha)
    assert not any(a.startswith("pr merge") for a in gh.argv())


def test_gh_merge_refuses_odd_numbers(gh, tmp_path):
    for n in ("--admin", "7", 0, -1, True, None):
        with pytest.raises(forge.ForgeError):
            forge.GhForge().merge(tmp_path, n, subject="t", head_sha="a" * 40)


def test_gh_nonzero_exit_is_forge_error(gh, tmp_path):
    for cmd in ("create", "view", "merge"):
        gh.out(cmd, "", "fail")
    g = forge.GhForge()
    with pytest.raises(forge.ForgeError, match="boom"):
        g.view(tmp_path, 7)
    with pytest.raises(forge.ForgeError, match="boom"):
        g.create(tmp_path, base="ark/x", head="ark/x-j", title="t", body="b")
    gh.view()
    with pytest.raises(forge.ForgeError, match="boom"):
        g.merge(tmp_path, 7, subject="t", head_sha="a" * 40)


def test_gh_bad_view_output(gh, tmp_path):
    for text in ["", "not json", "[]", "null", '{"state":"OPEN"}', json.dumps({**VIEW, "state": None}),
                 json.dumps({**VIEW, "headRefOid": 5})]:
        gh.out("view", text)
        with pytest.raises(forge.ForgeError):
            forge.GhForge().view(tmp_path, 7)


def test_gh_timeout_and_closed_stdin(gh, tmp_path, monkeypatch):
    gh.out("view", "", "sleep")
    t = time.monotonic()
    with pytest.raises(forge.ForgeError, match="timed out"):
        forge.GhForge().view(tmp_path, 7)
    assert time.monotonic() - t < 10
    gh.out("view", "", "stdin")   # reads stdin: must see EOF, not the caller's pipe
    with pytest.raises(forge.ForgeError):
        forge.GhForge().view(tmp_path, 7)   # empty output, but returns promptly


def test_gh_missing_or_unrunnable(gh, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(forge.ForgeError):
        forge.GhForge().view(tmp_path, 7)
    monkeypatch.setenv("PATH", f"{gh.dir / 'bin'}:/usr/bin:/bin")
    with pytest.raises(forge.ForgeError):
        forge.GhForge().view(tmp_path / "gone", 7)
    with pytest.raises(forge.ForgeError):
        forge.GhForge().create(tmp_path, base="ark/x", head="ark/x-j", title="a\0b", body="b")
    gh.out("create", "https://github.com/m/r/pull/7\n")
    forge.GhForge().create(tmp_path, base="ark/x", head="ark/x-j", title="t", body="\udcff")  # surrogate body


def test_local_merge_refuses_like_gh(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, base, "ark/x")
    head = branch_with_file(repo, tmp_path, "ark/x-job", base, "a.txt")
    f = forge.LocalForge()
    default = f.create(repo, base="master", head="ark/x-job", title="final", body="b")
    ark = f.create(repo, base="ark/x", head="ark/x-job", title="t", body="b")
    master_before = gitops.remote_sha(repo, "master")
    for n, sha in [(default["number"], head), (ark["number"], ""), (ark["number"], None),
                   (ark["number"], head[:10]), (0, head), (9, head), (-1, head)]:
        with pytest.raises(forge.ForgeError):
            f.merge(repo, n, subject="t", head_sha=sha)
    assert gitops.remote_sha(repo, "master") == master_before
    assert gitops.remote_sha(repo, "ark/x") == base
    f.merge(repo, ark["number"], subject="t", head_sha=head)
    with pytest.raises(forge.ForgeError):   # no longer OPEN
        f.merge(repo, ark["number"], subject="t", head_sha=head)
