"""bin/ark-test: the job gate runs only what a change can affect, and falls back to the whole suite when it cannot tell."""
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "ark-test"

STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    echo "$*" >> "$STUB_LOG"
    case "$*" in *--testmon-noselect*) echo data > .testmondata ;; esac
    case "$*" in *--testmon-noselect*) exit "${STUB_FULL_EXIT:-0}" ;; esac
    exit "${STUB_EXIT:-0}"
""")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class Repo(os.PathLike):
    def __init__(self, path, stub, log):
        self.path, self.stub, self.log = path, stub, log

    def __fspath__(self):
        return str(self.path)

    __str__ = __fspath__

    def __truediv__(self, other):
        return self.path / other


@pytest.fixture
def repo(tmp_path, ark_home):
    """A git repo with its own copy of bin/ark-test, a stub pytest, and a branch off `main`."""
    r = tmp_path / "repo"
    (r / "bin").mkdir(parents=True)
    (r / "lib").mkdir()
    shutil.copy(SCRIPT, r / "bin" / "ark-test")
    (r / "lib" / "a.py").write_text("x = 1\n")
    (r / ".gitignore").write_text(".testmondata*\n")
    stub = tmp_path / "stub-pytest"
    stub.write_text(STUB)
    stub.chmod(0o755)
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    git(r, "checkout", "-q", "-b", "job")
    return Repo(r, stub, tmp_path / "stub.log")


def run(repo, mode, *, testmon_data=True, **env):
    if testmon_data:
        (repo / ".testmondata").write_text("data\n")
    e = {**os.environ, "ARK_TEST_PYTEST": str(repo.stub), "ARK_TEST_BASE": "main", "STUB_LOG": str(repo.log), **env}
    p = subprocess.run([str(repo / "bin" / "ark-test"), mode], cwd=repo.path, env=e, capture_output=True, text=True)
    calls = repo.log.read_text().splitlines() if repo.log.exists() else []
    return p, calls


def commit(repo, name, text="y = 2\n"):
    f = repo / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", name)


def test_job_runs_only_the_selected_tests_for_a_python_change(repo):
    commit(repo, "lib/b.py")
    p, calls = run(repo, "job")
    assert p.stdout.splitlines()[0].startswith("ark-test: mode=job")
    assert calls == ["-q -n auto --testmon"]
    assert p.returncode == 0


def test_job_falls_back_to_full_when_there_is_no_testmon_data(repo):
    commit(repo, "lib/b.py")
    p, calls = run(repo, "job", testmon_data=False)
    assert p.stdout.splitlines()[0].startswith("ark-test: mode=full (no testmon data)")
    assert calls == ["-q -n auto --testmon-noselect"]


def test_job_seeds_the_data_from_the_shared_copy(repo, ark_home):
    commit(repo, "lib/b.py")
    shared = ark_home / "repos" / "arkimedes" / "testmondata"
    shared.parent.mkdir(parents=True)
    shared.write_text("shared\n")
    p, calls = run(repo, "job", testmon_data=False, ARK_HOME=str(ark_home))
    assert p.stdout.splitlines()[0].startswith("ark-test: mode=job")
    assert (repo / ".testmondata").read_text() == "shared\n"


@pytest.mark.parametrize("name", ["requirements.txt", "bin/tool", "docs/a.md", "lib/data.yaml", "setup.py.bak"])
def test_job_falls_back_to_full_when_a_file_is_not_python_under_lib_or_tests(repo, name):
    commit(repo, name)
    p, calls = run(repo, "job")
    assert p.stdout.splitlines()[0].startswith("ark-test: mode=full (changed outside")
    assert name in p.stdout.splitlines()[0]
    assert calls == ["-q -n auto --testmon-noselect"]


def test_job_counts_an_untracked_file_as_a_change(repo):
    (repo / "notes.txt").write_text("x\n")
    p, calls = run(repo, "job")
    assert "mode=full (changed outside" in p.stdout


def test_job_falls_back_to_full_when_testmon_itself_fails(repo):
    commit(repo, "lib/b.py")
    p, calls = run(repo, "job", STUB_EXIT="3")
    assert calls == ["-q -n auto --testmon", "-q -n auto --testmon-noselect"]
    assert "mode=full (testmon failed with exit 3)" in p.stdout


def test_job_keeps_a_red_test_red_without_a_second_run(repo):
    commit(repo, "lib/b.py")
    p, calls = run(repo, "job", STUB_EXIT="1")
    assert p.returncode == 1
    assert calls == ["-q -n auto --testmon"]


def test_job_with_nothing_selected_is_green(repo):
    commit(repo, "lib/b.py")
    p, calls = run(repo, "job", STUB_EXIT="5")
    assert p.returncode == 0


def test_full_runs_everything_and_shares_the_data_after_a_green_run(repo, ark_home):
    p, calls = run(repo, "full", testmon_data=False, ARK_HOME=str(ark_home))
    assert p.stdout.splitlines()[0].startswith("ark-test: mode=full")
    assert calls == ["-q -n auto --testmon-noselect"]
    assert (ark_home / "repos" / "arkimedes" / "testmondata").read_text() == "data\n"


def test_full_does_not_share_the_data_after_a_red_run(repo, ark_home):
    p, calls = run(repo, "full", testmon_data=False, ARK_HOME=str(ark_home), STUB_FULL_EXIT="1")
    assert p.returncode == 1
    assert not (ark_home / "repos" / "arkimedes" / "testmondata").exists()


def test_a_wrong_mode_is_a_usage_error(repo):
    p, calls = run(repo, "fast")
    assert p.returncode == 2 and calls == []
