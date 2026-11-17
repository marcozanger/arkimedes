import os
import pwd
import shutil
import tempfile
from pathlib import Path as _Path

# Suite sandbox: plain os.environ, before anything else, so monkeypatch.undo() lands HERE and never on
# real values. A test that loses its patches sees an empty home and shims named gh and claude.
_REAL_CLAUDE_DIR = _Path(pwd.getpwuid(os.getuid()).pw_dir).resolve() / ".claude"  # from the OS, not from $HOME
_SANDBOX = _Path(tempfile.mkdtemp(prefix="ark-suite-")).resolve()
_TRIPWIRE = _SANDBOX / "tripwire"
(_SANDBOX / "home").mkdir()
(_SANDBOX / "bin").mkdir()
for _name in ("gh", "claude"):
    _shim = _SANDBOX / "bin" / _name
    _shim.write_text(f'#!/bin/sh\necho "{_name} $*" >> "${{ARK_TRIPWIRE:-{_TRIPWIRE}}}"\nexit 97\n')
    _shim.chmod(0o755)
os.environ.update(HOME=str(_SANDBOX / "home"), ARK_HOME=str(_SANDBOX / "ark"), ARK_WORKTREES=str(_SANDBOX / "wt"),
                  PATH=f"{_SANDBOX / 'bin'}:{os.environ['PATH']}")
for _k in ("ARK_CLAUDE", "ARK_FORGE", "ARK_ROLE", "ARK_TRIPWIRE", "FAKE_STATE", "ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
           "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):  # a suite run from a git hook must not act on that repo
    os.environ.pop(_k, None)

import json
import subprocess

import pytest

from helpers import approve, plan_text


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def no_start_check(monkeypatch):
    """Tests that predate the start check: a new run counts its base as green. test_refresh_loop turns it on."""
    from ark import loop
    monkeypatch.setattr(loop, "START_CHECK", False)


@pytest.fixture(autouse=True)
def no_load_brake(monkeypatch):
    """The machine's load never holds a test's gate, and no load wait leaks from one test to the next.
    Tests of the brake set their own load (test_gate_brake's clock)."""
    from ark import loop
    loop._load_wait.clear()
    monkeypatch.setattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0))
    yield
    loop._load_wait.clear()


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


def _assert_contained():
    from ark import paths
    for p in (paths.ark_home(), paths.worktrees_dir()):
        assert not p.resolve().is_relative_to(_REAL_CLAUDE_DIR), f"a test reached the real home: {p}"


@pytest.fixture(autouse=True)
def _suite_guard():
    """Checks before and after each test, so a test that drops the env fails even if nothing else looks."""
    _assert_contained()
    yield
    _assert_contained()


def pytest_sessionfinish(session, exitstatus):
    """A shim call inside a try/except would pass its test; the run still fails here."""
    if _TRIPWIRE.exists():
        print(f"\nreal program called during the suite:\n{_TRIPWIRE.read_text()}")
        session.exitstatus = 1
    shutil.rmtree(_SANDBOX, ignore_errors=True)


@pytest.fixture
def feature(runner, make_feature):
    """A seeded repo and an approved plan. test_loop.py overrides this with its own copy."""
    def make(m=None, repo="demo", slug="notes-edit", tasks=("T1", "T2", "T3")):
        r = runner(repo)
        p = make_feature(plan_text(m, tasks=tasks, repo=repo, slug=slug), repo=repo, slug=slug, create_repo=False)
        approve(p, repo, slug)
        return r, p
    return make
