import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from control_helpers import SLUG, ark, drive, job_status, settled, state

from ark import ctl, inbox, loop


@pytest.fixture
def terminal(feature, git_identity, monkeypatch, tmp_path):
    """Marco at a terminal: `claude` is a stub that commits, while the runner's processes still get the fake,
    and the command's wait runs the loop that would run beside it. Returns the file with the stub's arguments."""
    fake = os.environ["ARK_CLAUDE"]
    seen = tmp_path / "attached.argv"
    stub = tmp_path / "claude-stub"
    stub.write_text(f"""#!/bin/sh
if [ -n "$ARK_ROLE" ]; then exec "{fake}" "$@"; fi
printf '%s\\n' "$@" > "{seen}"
mkdir -p src/shell
echo attached > src/shell/attached.txt
if [ -n "$ATTACH_DIRTY" ]; then exit 0; fi
git add -A
git commit -qm attached
""")
    stub.chmod(0o755)
    monkeypatch.setenv("ARK_CLAUDE", str(stub))
    monkeypatch.setattr(ctl, "_sleep", lambda s: loop.cycle())
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    return seen


def worker_attempts(job):
    return len(list(Path(os.environ["FAKE_STATE"]).glob(f"{job}.worker.*.prompt")))


def test_attach_takes_a_running_worker_and_the_gates_follow(feature, terminal, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FAKE_HOLD", str(tmp_path / "never"))
    feature()
    drive(lambda: job_status("shell") == "working" and worker_attempts("shell") == 1)  # it is really running
    capsys.readouterr()
    code, out = ark(capsys, "attach", "shell")
    assert code == 0, out
    args = terminal.read_text().split()
    assert args[0] == "--resume" and args[1] == state()["jobs"]["shell"]["session"]
    monkeypatch.setenv("FAKE_HOLD", "")
    drive(lambda: job_status("shell") == "merged")
    assert worker_attempts("shell") == 1        # no worker ran after him


def test_attach_takes_a_job_waiting_for_an_answer(feature, terminal, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    capsys.readouterr()
    assert ark(capsys, "attach", "shell")[0] == 0
    assert state()["questions"] == [] or job_status("shell") != "waiting_answer"
    drive(settled)
    assert job_status("shell") == "merged" and state()["questions"] == []


def test_uncommitted_work_keeps_the_job_with_him(feature, terminal, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    monkeypatch.setenv("ATTACH_DIRTY", "1")
    capsys.readouterr()
    code, out = ark(capsys, "attach", "shell")
    assert code == 1 and "commit" in out
    for _ in range(3):
        loop.cycle()
    assert job_status("shell") == "attached"
    monkeypatch.delenv("ATTACH_DIRTY")
    assert ark(capsys, "attach", "shell")[0] == 0
    drive(settled)
    assert job_status("shell") == "merged"


def test_attach_needs_a_terminal(feature, terminal, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    capsys.readouterr()
    code, out = ark(capsys, "attach", "shell")
    assert code == 1 and "terminal" in out and inbox.pending("demo", SLUG) == []


def test_attach_refuses_a_held_run_and_a_finished_job(feature, terminal, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    _, plan = feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    original = plan.read_text()
    plan.write_text(original + "\n")
    drive(lambda: state()["status"] == "held")
    capsys.readouterr()
    assert ark(capsys, "attach", "shell")[0] == 1
    plan.write_text(original)
    drive(lambda: state()["status"] == "running")
    inbox.post("demo", SLUG, "answer", id="q1", text=":stop")
    drive(settled)
    capsys.readouterr()
    assert ark(capsys, "attach", f"demo/{SLUG}:shell")[0] == 1


def test_no_loop_running_is_said(feature, terminal, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    monkeypatch.setattr(ctl, "_sleep", lambda s: None)
    capsys.readouterr()
    code, out = ark(capsys, "attach", "shell")
    assert code == 1 and "ark-loop" in out


def test_stop_ends_an_attached_job(feature, terminal, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_BLOCKED", "shell:1")
    monkeypatch.setenv("ATTACH_DIRTY", "1")
    feature()
    drive(lambda: job_status("shell") == "waiting_answer")
    ark(capsys, "attach", "shell")
    assert job_status("shell") == "attached"
    assert ark(capsys, "stop", "shell")[0] == 0
    drive(settled)
    assert job_status("shell") == "blocked"
