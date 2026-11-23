"""A fresh worker that takes over a job starts from a handoff the runner builds from git."""
import os
from pathlib import Path

import pytest
from control_helpers import drive, state
from helpers import no_load_brake

from ark import gitops


@pytest.fixture(autouse=True)
def _no_load_brake(ark_home):
    no_load_brake(ark_home)


def fake_file(name):
    return Path(os.environ["FAKE_STATE"]) / name


def test_an_escalated_worker_gets_the_handoff_of_one_that_died(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SUBJECT", "T1:")
    monkeypatch.setenv("FAKE_NOREPORT", "shell:1")
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "escalate", "note": "harder"}')
    feature()
    drive(lambda: fake_file("shell.worker.2.prompt").exists())
    first = fake_file("shell.worker.1.prompt").read_text()
    assert "Previous attempt" not in first and "UNTRUSTED" not in first
    second = fake_file("shell.worker.2.prompt").read_text()
    assert "## Previous attempt (its work is already in this worktree)" in second
    assert "Tasks with a commit: T1. Tasks without one: T2." in second
    assert "T1: shell attempt 1" in second and "left no report" in second
    assert state()["jobs"]["shell"]["tier_attempts"] <= 1  # a fresh worker one tier up, not a retry


def test_status_lines_lists_the_uncommitted_changes(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    gitops.git(repo, "init", "-q")
    assert gitops.status_lines(repo) == []
    (repo / "a.txt").write_text("x\n")
    assert gitops.status_lines(repo) == ["?? a.txt"]
