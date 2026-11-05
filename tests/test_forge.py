import subprocess

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


def test_get_picks_local_in_tests(runner):
    assert isinstance(forge.get(), forge.LocalForge)


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


def test_gh_forge_builds_commands(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = "https://github.com/m/r/pull/7\n" if cmd[:3] == ["gh", "pr", "create"] else \
            '{"baseRefName":"ark/x","headRefName":"ark/x-j","headRefOid":"abc","state":"OPEN","url":"u"}'
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(forge.subprocess, "run", fake_run)
    g = forge.GhForge()
    assert g.create("/r", base="ark/x", head="ark/x-j", title="t", body="b") == \
        {"number": 7, "url": "https://github.com/m/r/pull/7"}
    assert g.view("/r", 7)["head_sha"] == "abc"
    g.merge("/r", 7, subject="t", head_sha="abc")
    assert calls[-1] == ["gh", "pr", "merge", "7", "--squash", "--subject", "t", "--body", "",
                         "--match-head-commit", "abc"]
