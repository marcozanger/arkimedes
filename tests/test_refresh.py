import json
import subprocess
from pathlib import Path

import pytest

from conftest import git
from helpers import manifest
from test_loop import SLUG, feature  # noqa: F401  (test_loop's own feature fixture)

from ark import gitops, loop, paths


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    git("init", "-q", "-b", "master", cwd=r)
    (r / "a.txt").write_text("one\n")
    git("add", "-A", cwd=r)
    git("commit", "-q", "-m", "a", cwd=r)
    return r


def test_merge_keep_makes_a_two_parent_commit(tmp_path):
    r = _repo(tmp_path)
    git("checkout", "-q", "-b", "base", cwd=r)
    (r / "b.txt").write_text("base\n")
    git("add", "-A", cwd=r)
    git("commit", "-q", "-m", "b", cwd=r)
    git("checkout", "-q", "master", cwd=r)
    (r / "c.txt").write_text("main\n")
    git("add", "-A", cwd=r)
    git("commit", "-q", "-m", "c", cwd=r)
    base, main = gitops.rev(r, "base"), gitops.rev(r, "master")
    sha, conflicts = gitops.merge_keep(r, base, main, "refresh: main", tmp_path / "s")
    assert conflicts == []
    assert gitops.git(r, "rev-list", "--parents", "-n", "1", sha).split()[1:] == [base, main]
    gitops._cleanup(r, tmp_path / "s")


def test_merge_keep_lists_conflicting_files(tmp_path):
    r = _repo(tmp_path)
    git("checkout", "-q", "-b", "base", cwd=r)
    (r / "a.txt").write_text("base\n")
    git("commit", "-q", "-am", "base", cwd=r)
    git("checkout", "-q", "master", cwd=r)
    (r / "a.txt").write_text("main\n")
    git("commit", "-q", "-am", "main", cwd=r)
    sha, conflicts = gitops.merge_keep(r, gitops.rev(r, "base"), gitops.rev(r, "master"), "m", tmp_path / "s")
    assert sha is None and conflicts == ["a.txt"]
    gitops._cleanup(r, tmp_path / "s")


def test_merge_keep_with_main_already_in_base_returns_the_base(tmp_path):
    r = _repo(tmp_path)
    base = gitops.rev(r, "master")
    sha, conflicts = gitops.merge_keep(r, base, base, "m", tmp_path / "s")
    assert (sha, conflicts) == (base, [])
    gitops._cleanup(r, tmp_path / "s")


# ---- ark-refresh ---------------------------------------------------------------------------------

def _run_bin(*args):
    p = subprocess.run([str(paths.code_dir() / "bin" / "ark-refresh"), *args], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def _commit(repo, branch, change):
    rel, text = change
    git("checkout", "-q", branch, cwd=repo)
    (repo / rel).write_text(text)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", f"change {rel}", cwd=repo)
    git("push", "-q", "origin", branch, cwd=repo)


@pytest.fixture
def started_run(feature):  # noqa: F811
    """A run whose ark/<slug> exists, then `base_change` on it and `main_change` on master, both pushed:
    (repo, main sha)."""
    def make(main_change, base_change=None, gate="true", setup=()):
        repo, _ = feature(manifest(integration_gate=gate, setup=list(setup)))
        loop.cycle()  # the run starts: ark/<slug> exists on origin
        git("fetch", "-q", "origin", cwd=repo)
        git("branch", "-f", f"ark/{SLUG}", f"origin/ark/{SLUG}", cwd=repo)
        if base_change:
            _commit(repo, f"ark/{SLUG}", base_change)
        _commit(repo, "master", main_change)
        return repo, gitops.rev(repo, "master")
    return make


def _origin(repo):
    return gitops.git(repo, "ls-remote", "origin")


def test_green_merge_reports_the_merge_commit(started_run):
    repo, main = started_run(main_change=("c.txt", "main\n"), base_change=("b.txt", "base\n"))
    before = _origin(repo)
    code, out = _run_bin(f"demo/{SLUG}", main)
    assert code == 0 and out["ok"] and out["merged"] and out["conflict"] == [] and out["main_red"] is None
    assert gitops.git(repo, "rev-list", "--parents", "-n", "1", out["merged"]).split()[1:] == [out["base"], main]
    assert _origin(repo) == before  # nothing reaches origin
    assert not (paths.worktrees_dir() / "demo" / SLUG / "_refresh-check").exists()


def test_main_already_in_the_base_is_green_on_the_base(started_run):
    repo, _ = started_run(main_change=("c.txt", "main\n"))
    base = gitops.rev(repo, f"origin/ark/{SLUG}")
    code, out = _run_bin(f"demo/{SLUG}", base)
    assert code == 0 and out["ok"] and out["merged"] == base


def test_conflict_lists_files_and_runs_no_gate(started_run):
    repo, main = started_run(main_change=("a.txt", "main\n"), base_change=("a.txt", "base\n"),
                             gate="touch ran && false")
    before = _origin(repo)
    code, out = _run_bin(f"demo/{SLUG}", main)
    assert code == 1 and out["conflict"] == ["a.txt"] and out["main_red"] is None and out["merged"] is None
    assert "does not merge cleanly" in out["reason"]
    assert not list(paths.worktrees_dir().glob("**/ran"))
    assert _origin(repo) == before


def test_a_gate_red_on_main_alone_is_main_red(started_run):
    repo, main = started_run(main_change=("bad.txt", "x\n"), gate="test ! -e bad.txt")
    code, out = _run_bin(f"demo/{SLUG}", main)
    assert code == 1 and out["ok"] is False and out["main_red"] is True


def test_a_gate_red_only_on_the_merge_is_a_clash(started_run):
    repo, main = started_run(main_change=("m.txt", "x\n"), base_change=("b.txt", "y\n"),
                             gate="test ! -e m.txt -o ! -e b.txt")
    before = _origin(repo)
    code, out = _run_bin(f"demo/{SLUG}", main)
    assert code == 1 and out["ok"] is False and out["main_red"] is False and out["merged"]
    assert "integration gate failed" in out["reason"]
    assert _origin(repo) == before


def test_a_red_setup_step_cannot_tell_main_red(started_run):
    _, main = started_run(main_change=("c.txt", "x\n"), setup=["echo nope; false"])
    code, out = _run_bin(f"demo/{SLUG}", main)
    assert code == 1 and out["main_red"] is None and "setup" in out["reason"] and "nope" in out["tail"]


def test_refuses_a_main_sha_that_does_not_exist(started_run):
    started_run(main_change=("c.txt", "x\n"))
    code, out = _run_bin(f"demo/{SLUG}", "0" * 40)
    assert code == 1 and "main" in out["reason"] and out["ok"] is False


def test_refuses_a_main_that_is_not_a_full_sha(started_run):
    started_run(main_change=("c.txt", "x\n"))
    code, out = _run_bin(f"demo/{SLUG}", "master")
    assert code == 1 and "main" in out["reason"]


def test_refuses_a_run_that_does_not_exist(started_run):
    _, main = started_run(main_change=("c.txt", "x\n"))
    code, out = _run_bin("demo/other", main)
    assert code == 1 and out["ok"] is False and out["reason"]


def test_prints_its_usage():
    p = subprocess.run([str(paths.code_dir() / "bin" / "ark-refresh")], capture_output=True, text=True)
    assert p.returncode == 2 and "uso: ark-refresh <repo>/<slug> <main-sha>" in p.stderr
