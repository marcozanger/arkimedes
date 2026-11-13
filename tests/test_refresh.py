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
