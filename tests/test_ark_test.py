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
    [ -n "${STUB_OUT:-}" ] && echo "$STUB_OUT"
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


def test_job_runs_the_tests_that_name_a_changed_doc_besides_the_selection(repo):
    commit(repo, "tests/test_a.py", "# reads docs/a.md\n")
    commit(repo, "tests/test_b.py", "# unrelated\n")
    commit(repo, "docs/a.md")
    p, calls = run(repo, "job")
    assert p.stdout.splitlines()[0].startswith("ark-test: mode=job")
    assert calls == ["-q -n auto --testmon", "-q -n auto tests/test_a.py"]
    assert p.returncode == 0


def test_job_names_a_test_by_the_basename_of_the_changed_file(repo):
    commit(repo, "tests/test_a.py", "# reads a.md\n")
    commit(repo, "docs/a.md")
    p, calls = run(repo, "job")
    assert calls == ["-q -n auto --testmon", "-q -n auto tests/test_a.py"]


def test_job_runs_only_the_selection_when_no_test_names_the_file(repo):
    commit(repo, "tests/test_b.py", "# unrelated\n")
    commit(repo, "docs/a.md")
    p, calls = run(repo, "job", STUB_EXIT="5")
    assert calls == ["-q -n auto --testmon"]
    assert p.returncode == 0


@pytest.mark.parametrize("name", ["requirements.txt", "pytest.ini", "bin/ark-test"])
def test_job_falls_back_to_full_when_a_forcing_file_changes(repo, name):
    commit(repo, name, (repo / name).read_text() + "# changed\n" if (repo / name).exists() else "# changed\n")
    p, calls = run(repo, "job")
    assert p.stdout.splitlines()[0].startswith("ark-test: mode=full (changed a file that forces")
    assert name in p.stdout.splitlines()[0]
    assert calls == ["-q -n auto --testmon-noselect"]


def test_job_treats_a_deleted_non_python_file_like_a_changed_one(repo):
    commit(repo, "docs/a.md")
    commit(repo, "tests/test_a.py", "# reads docs/a.md\n")
    git(repo, "branch", "-f", "main", "job")
    git(repo, "checkout", "-q", "-b", "job2")
    git(repo, "rm", "-q", "docs/a.md")
    git(repo, "commit", "-q", "-m", "rm")
    p, calls = run(repo, "job")
    assert calls == ["-q -n auto --testmon", "-q -n auto tests/test_a.py"]


def test_job_is_red_when_the_named_tests_fail(repo):
    commit(repo, "tests/test_a.py", "# reads docs/a.md\n")
    commit(repo, "docs/a.md")
    stub = repo.path.parent / "stub2"
    stub.write_text("#!/usr/bin/env bash\ncase \"$*\" in *test_a.py*) exit 1 ;; esac\nexit 0\n")
    stub.chmod(0o755)
    repo.stub = stub
    p, calls = run(repo, "job")
    assert p.returncode == 1


def test_job_counts_an_untracked_file_as_a_change(repo):
    (repo / "pytest.ini").write_text("x\n")
    p, calls = run(repo, "job")
    assert "mode=full (changed a file that forces" in p.stdout


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


def last(p):
    return p.stdout.splitlines()[-1]


def test_summary_line_for_job_counts_the_tests_run(repo):
    commit(repo, "lib/b.py")
    p, _ = run(repo, "job", STUB_OUT="12 passed, 1 failed, 2 skipped, 1 error in 3.00s")
    assert last(p) == "ark-test: mode=job tests=16 (only the tests the change can affect)"
    assert p.stdout.splitlines()[0].startswith("ark-test: mode=job")


def test_summary_line_for_job_adds_the_named_test_files(repo):
    commit(repo, "tests/test_a.py", "# reads docs/a.md\n")
    commit(repo, "docs/a.md")
    p, _ = run(repo, "job", STUB_OUT="3 passed in 1s")
    assert last(p).startswith("ark-test: mode=job tests=6 (")


def test_summary_line_for_full(repo):
    p, _ = run(repo, "full", testmon_data=False, STUB_OUT="40 passed in 9s")
    assert last(p) == "ark-test: mode=full tests=40 (integration gate, everything runs)"


@pytest.mark.parametrize("setup,why", [
    (lambda r: None, "no testmon data"),
    (lambda r: commit(r, "pytest.ini", "x\n"), "changed a file that forces the whole suite: pytest.ini"),
])
def test_summary_line_for_full_fallbacks(repo, setup, why):
    setup(repo)
    p, _ = run(repo, "job", testmon_data=why != "no testmon data", STUB_OUT="7 passed in 1s")
    assert last(p) == f"ark-test: mode=full tests=7 ({why})"


def test_summary_line_after_testmon_failure_reports_the_full_run(repo):
    commit(repo, "lib/b.py")
    p, _ = run(repo, "job", STUB_EXIT="3", STUB_OUT="5 passed in 1s")
    assert last(p) == "ark-test: mode=full tests=5 (testmon failed with exit 3)"


def test_summary_line_is_a_question_mark_when_the_count_cannot_be_read(repo):
    commit(repo, "lib/b.py")
    p, _ = run(repo, "job")
    assert last(p).startswith("ark-test: mode=job tests=? (")


def test_summary_line_keeps_the_exit_code(repo):
    commit(repo, "lib/b.py")
    p, _ = run(repo, "job", STUB_EXIT="1", STUB_OUT="1 failed in 1s")
    assert p.returncode == 1 and last(p).startswith("ark-test: mode=job tests=1 (")
