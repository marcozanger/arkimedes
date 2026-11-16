import os

import pytest

from ark import paths


def test_paths_follow_ark_home(ark_home):
    assert paths.ark_home() == ark_home
    assert paths.approval_file("muscleapp/mobile", "x") == ark_home / "approvals/muscleapp/mobile/x.json"
    assert paths.run_file("demo", "x") == ark_home / "runs/demo/x.md"


def test_repo_name_is_the_inverse_of_find_repo(make_repo, tmp_path):
    deep = make_repo("gl/proposals/v0")
    assert paths.repo_name(deep) == "gl/proposals/v0"
    assert paths.repo_name(make_repo("demo")) == "demo"
    assert paths.repo_name(tmp_path) is None                      # not under a root
    assert paths.repo_name(tmp_path / "ws" / "gl") is None        # under a root, not a repo


def test_workspace_accounts_default_to_none(ark_home):
    assert paths.load_workspace()["accounts"] == []


def test_find_repo_needs_origin(make_repo):
    with_origin = make_repo("demo")
    make_repo("local-only", origin=False)
    assert paths.find_repo("demo") == with_origin
    assert paths.find_repo("local-only") is None
    assert paths.find_repo("missing") is None


def test_find_repo_up_to_three_levels(make_repo):
    nested = make_repo("muscleapp/mobile")
    deep = make_repo("globallogic/proposals/v0")
    assert paths.find_repo("muscleapp/mobile") == nested
    assert paths.find_repo("globallogic/proposals/v0") == deep
    assert paths.find_repo("globallogic/proposals") is None   # a folder above a repo is not one
    assert paths.find_repo("../demo") is None
    assert paths.find_repo("a/b/c/d") is None


def test_excluded_repo_is_not_found(make_repo, ark_home):
    make_repo("demo")
    (ark_home / "workspace.json").write_text(
        '{"roots": ["%s"], "exclude": ["demo"]}' % (ark_home.parent / "ws"))
    assert paths.find_repo("demo") is None


import json


def test_workspace_defaults_fill_missing_keys(ark_home):
    ws = paths.load_workspace()
    assert ws["limits"] == {"writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1, "gates_total": 2, "refresh_usd": 3,
                            "gate_workers": 4, "load_max": paths.default_load_max(), "load_wait_max": 900}
    assert ws["tick_minutes"] == 15 and ws["poll_seconds"] == 15


def test_workspace_partial_limits_keep_other_defaults(ark_home):
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "limits": {"writers_total": 1}}))
    ws = paths.load_workspace()
    assert ws["limits"]["writers_total"] == 1
    assert ws["limits"]["writers_per_repo"] == 3
    assert paths.load_workspace()["limits"]["writers_total"] == 1  # defaults never mutated


def test_runner_paths(ark_home, tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_WORKTREES", str(tmp_path / "wt"))
    assert paths.run_dir("muscleapp/mobile", "x") == ark_home / "runs/muscleapp/mobile/x"
    assert paths.worktrees_dir() == tmp_path / "wt"
    assert (paths.code_dir() / "lib/ark/paths.py").exists()


def open_fds():
    return len(os.listdir("/dev/fd"))


@pytest.mark.parametrize("plant", [lambda p: p.mkdir(), os.mkfifo, lambda p: p.symlink_to("/etc/hosts")],
                         ids=["directory", "fifo", "link"])
def test_whatever_is_not_a_regular_file_refuses_and_leaks_no_descriptor(tmp_path, plant):
    plant(tmp_path / "x.proc")
    before = open_fds()
    for _ in range(20):
        with pytest.raises(OSError):
            paths.read_regular(tmp_path / "x.proc")
    assert open_fds() == before


def test_read_regular_of_a_file_closes_it(tmp_path):
    (tmp_path / "f").write_bytes(b"hello")
    before = open_fds()
    for _ in range(20):
        assert paths.read_regular(tmp_path / "f", 2) == b"he"
    assert open_fds() == before


def test_load_max_default_is_a_bit_above_the_cores(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert paths.default_load_max() == 10
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    assert paths.default_load_max() == 2
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert paths.default_load_max() == 2


def test_an_old_workspace_without_the_new_limits_still_loads(ark_home):
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "limits": {"heavy_gates": 2}}))
    lim = paths.load_workspace()["limits"]
    assert lim["heavy_gates"] == 2 and lim["gate_workers"] == 4 and lim["load_wait_max"] == 900


def test_workspace_overrides_the_gate_limits(ark_home):
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "limits": {"gate_workers": 2, "load_max": 0}}))
    lim = paths.load_workspace()["limits"]
    assert lim["gate_workers"] == 2 and lim["load_max"] == 0
