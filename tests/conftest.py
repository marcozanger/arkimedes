import json
import subprocess

import pytest

from helpers import plan_text


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def ark_home(tmp_path, monkeypatch):
    """An empty Arkimedes home whose only workspace root is tmp_path/ws."""
    home = tmp_path / "ark"
    home.mkdir()
    (tmp_path / "ws").mkdir()
    (home / "workspace.json").write_text(json.dumps({"roots": [str(tmp_path / "ws")], "exclude": []}))
    monkeypatch.setenv("ARK_HOME", str(home))
    monkeypatch.delenv("ARK_ROLE", raising=False)
    return home


@pytest.fixture
def make_repo(ark_home, tmp_path):
    def make(name="demo", origin=True):
        repo = tmp_path / "ws" / name
        repo.mkdir(parents=True)
        git("init", "-q", "-b", "master", cwd=repo)
        if origin:
            bare = tmp_path / "remotes" / (name.replace("/", "-") + ".git")
            bare.mkdir(parents=True)
            git("init", "-q", "--bare", cwd=bare)
            git("remote", "add", "origin", str(bare), cwd=repo)
        return repo
    return make


@pytest.fixture
def make_feature(ark_home, make_repo, tmp_path):
    """Write desk/<repo>/<slug>/plan.md (and spec.md unless spec_status is None)."""
    def make(text=None, *, repo="demo", slug="notes-edit", spec_status="approved", create_repo=True):
        if create_repo and not (tmp_path / "ws" / repo).exists():
            make_repo(repo)
        d = ark_home / "desk" / repo / slug
        d.mkdir(parents=True, exist_ok=True)
        if spec_status:
            (d / "spec.md").write_text(
                f"---\nstatus: {spec_status}\nrepo: {repo}\nslug: {slug}\ncreated: 2026-11-05\n---\n# Spec\n")
        p = d / "plan.md"
        p.write_text(text if text is not None else plan_text(repo=repo, slug=slug))
        return p
    return make


from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                 "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def runner(ark_home, make_repo, tmp_path, monkeypatch):
    """Runner test setup: fake claude, local forge, worktrees under tmp. Returns seed(name) that
    creates a repo with one commit on master, pushed, origin/HEAD set."""
    monkeypatch.setenv("ARK_WORKTREES", str(tmp_path / "wt"))
    monkeypatch.setenv("ARK_FORGE", "local")
    monkeypatch.setenv("ARK_CLAUDE", str(CODE / "tests" / "fake_claude.py"))
    (tmp_path / "fake").mkdir()
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "fake"))
    for k in ("FAKE_FAIL", "FAKE_OUTSIDE", "FAKE_FIX", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    def seed(name="demo"):
        repo = make_repo(name)
        (repo / "README.md").write_text(f"{name}\n")
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", "init", cwd=repo)
        git("push", "-q", "origin", "master", cwd=repo)
        git("remote", "set-head", "origin", "master", cwd=repo)
        return repo
    return seed
