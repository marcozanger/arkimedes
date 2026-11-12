import shutil
import subprocess
import sys

import pytest
from test_loop import SLUG, drive, feature, held, state  # noqa: F401

from ark import loop, notify, paths, proc  # noqa: F401


def test_the_code_version_changes_with_the_code(tmp_path):
    code = tmp_path / "code"
    shutil.copytree(paths.code_dir() / "lib", code / "lib")
    (code / "bin").mkdir()
    shutil.copy(paths.code_dir() / "bin" / "ark-loop", code / "bin")
    v1 = loop.code_version(code)
    (code / "lib" / "ark" / "loop.py").write_text((code / "lib" / "ark" / "loop.py").read_text() + "\n# x\n")
    assert loop.code_version(code) != v1


def test_self_check_passes_on_this_code(ark_home):
    assert loop._self_check(paths.code_dir()) is None


def test_self_check_reports_broken_code(tmp_path, ark_home):
    code = tmp_path / "code"
    shutil.copytree(paths.code_dir() / "lib", code / "lib")
    (code / "lib" / "ark" / "brief.py").write_text("def broken(:\n")
    assert "SyntaxError" in loop._self_check(code)


def test_new_good_code_replaces_the_process(monkeypatch, ark_home):
    calls = []
    monkeypatch.setattr(loop, "code_version", lambda code=None: "new")
    monkeypatch.setattr(loop, "_self_check", lambda code=None: None)
    monkeypatch.setattr(loop.os, "execv", lambda exe, argv: calls.append(argv))
    monkeypatch.setattr(notify, "deliver", lambda alerts, critical: None)
    loop._maybe_restart({"version": "old", "bad": None}, ["--x"])
    assert calls == [[sys.executable, "-m", "ark.loop", "--x"]]


def test_broken_new_code_keeps_the_old_loop_and_alerts_once(monkeypatch, ark_home):
    sent = []
    monkeypatch.setattr(loop, "code_version", lambda code=None: "new")
    monkeypatch.setattr(loop, "_self_check", lambda code=None: "SyntaxError: bad")
    monkeypatch.setattr(loop.os, "execv", lambda exe, argv: pytest.fail("must not restart"))
    monkeypatch.setattr(notify, "deliver", lambda alerts, critical: sent.append(critical))
    started = {"version": "old", "bad": None}
    loop._maybe_restart(started, [])
    loop._maybe_restart(started, [])
    assert len(sent) == 1 and "SyntaxError" in sent[0][0]


def test_a_worker_alive_across_a_restart_is_picked_up(feature, held, monkeypatch):
    feature()
    drive(lambda: state()["jobs"]["shell"]["status"] == "working")
    # a fresh loop: nothing in memory, only the state and the .proc files
    import importlib
    importlib.reload(loop)
    held.touch()
    drive(lambda: state()["jobs"]["shell"]["status"] not in ("working", "needs_worker"))
    assert state()["jobs"]["shell"]["attempts"] == 1


def test_self_check_mode_exits_zero(ark_home):
    r = subprocess.run([sys.executable, "-m", "ark.loop", "--self-check"], cwd=paths.code_dir(),
                       env={**__import__("os").environ, "PYTHONPATH": str(paths.code_dir() / "lib")},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ok" in r.stdout
