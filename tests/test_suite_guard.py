"""The suite can never reach the real home, the real `claude` or the real `gh`, even when a test
loses its patches. Each layer is tested alone, and the checks are seen firing in a child pytest."""
import os
import subprocess
import sys

import pytest

import conftest
from ark import claude, forge, loop, paths

ALARM = "a test reached the real home"


def in_sandbox(path):
    return path.resolve().is_relative_to(conftest._SANDBOX)


def test_undo_lands_in_the_sandbox(ark_home, monkeypatch):
    monkeypatch.undo()
    assert in_sandbox(paths.ark_home()) and in_sandbox(paths.worktrees_dir())
    assert claude.claude_bin() == str(conftest._SANDBOX / "bin" / "claude")
    assert isinstance(forge.get(), forge.GhForge)


def test_dropping_the_home_variables_falls_back_into_the_sandbox(monkeypatch):
    monkeypatch.delenv("ARK_HOME")
    monkeypatch.delenv("ARK_WORKTREES")
    assert in_sandbox(paths.ark_home()) and in_sandbox(paths.worktrees_dir())


def test_a_child_that_inherits_the_environment_stays_in_the_sandbox():
    code = "from pathlib import Path; print(Path.home())"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert in_sandbox(type(conftest._SANDBOX)(out.strip()))


@pytest.mark.parametrize("name", ["gh", "claude"])
def test_a_real_program_name_hits_the_tripwire_shim(name, tmp_path):
    own = tmp_path / "tripwire"                                                 # the session's own record is not ours to touch
    env = {**os.environ, "ARK_TRIPWIRE": str(own)}
    assert subprocess.run([name, "pr", "list"], capture_output=True, env=env).returncode == 97
    assert f"{name} pr list" in own.read_text()


def test_the_incident_itself_runs_nothing_real(monkeypatch):
    """Fixtures undone, then the loop runs: the first incident, and now it sees an empty sandbox."""
    monkeypatch.undo()
    assert in_sandbox(paths.ark_home())
    assert loop.cycle(forge=object())[0] == "idle"


def child_pytest(tmp_path, body, **extra_env):
    """A whole pytest session under the real conftest, with its own sandbox."""
    (tmp_path / "test_probe.py").write_text(body)
    code = conftest.CODE
    env = {**os.environ, "PYTHONPATH": f"{code / 'tests'}:{code / 'lib'}", **extra_env}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(code / "pytest.ini"), "--rootdir", str(tmp_path), "-o", "addopts=",
         "--confcutdir", str(tmp_path), "-p", "conftest", "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "base"),
         "test_probe.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)


# Each probe only asks where the paths lead; nothing in them writes or starts anything.
DROPPED = """
import os
import conftest
def test_it():
    {drop}
"""


@pytest.mark.parametrize("drop", [
    'os.environ["HOME"] = str(conftest._REAL_CLAUDE_DIR.parent); del os.environ["ARK_HOME"]',
    'os.environ["HOME"] = str(conftest._REAL_CLAUDE_DIR.parent); del os.environ["ARK_WORKTREES"]',
    'del os.environ["HOME"]; del os.environ["ARK_HOME"]',
    'del os.environ["HOME"]; del os.environ["ARK_WORKTREES"]',
    'os.environ["ARK_HOME"] = str(conftest._REAL_CLAUDE_DIR / "arkimedes")',
    'os.environ["ARK_WORKTREES"] = str(conftest._REAL_CLAUDE_DIR / "ark-worktrees")',
])
def test_the_guard_fires_when_a_test_drops_the_env(tmp_path, drop):
    r = child_pytest(tmp_path, DROPPED.format(drop=drop))
    assert r.returncode != 0 and ALARM in r.stdout, r.stdout + r.stderr


def test_the_guard_also_fires_before_a_test_that_starts_with_the_env_gone(tmp_path):
    body = 'import os, conftest\nos.environ["ARK_HOME"] = str(conftest._REAL_CLAUDE_DIR / "arkimedes")\n' \
           'def test_it():\n    raise SystemExit("this test ran")\n'
    r = child_pytest(tmp_path, body)
    assert r.returncode != 0 and ALARM in r.stdout and "this test ran" not in r.stdout, r.stdout + r.stderr


def test_a_session_where_nothing_went_wrong_passes(tmp_path):
    r = child_pytest(tmp_path, "def test_it():\n    pass\n")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("name", ["gh", "claude"])
def test_a_swallowed_call_to_a_real_program_still_fails_the_session(tmp_path, name):
    body = f'import subprocess\ndef test_it():\n    subprocess.run(["{name}", "x"], capture_output=True)\n'
    r = child_pytest(tmp_path, body)
    assert r.returncode == 1 and "1 passed" in r.stdout and f"{name} x" in r.stdout + r.stderr, r.stdout + r.stderr


def test_the_guards_own_test_does_not_erase_what_another_test_tripped(tmp_path):
    body = ('import subprocess\n'
            'def test_swallowed():\n    subprocess.run(["gh", "pr", "merge", "1"], capture_output=True)\n'
            'from test_suite_guard import test_a_real_program_name_hits_the_tripwire_shim\n')
    r = child_pytest(tmp_path, body)
    assert r.returncode == 1 and "gh pr merge 1" in r.stdout + r.stderr, r.stdout + r.stderr


@pytest.mark.parametrize("var", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"])
def test_a_suite_run_from_a_git_hook_does_not_inherit_its_repo(tmp_path, var):
    body = f'import os\ndef test_it():\n    assert "{var}" not in os.environ\n'
    r = child_pytest(tmp_path, body, **{var: str(tmp_path / "elsewhere")})
    assert r.returncode == 0, r.stdout + r.stderr
