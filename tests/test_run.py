from ark import desk, paths, run

APPROVAL = {"repo": "demo", "slug": "notes-edit", "plan": "/x/plan.md", "sha256": "ab" * 32,
            "approved_at": "2026-11-05T10:00:00+00:00"}


def test_new_state_is_waiting_without_jobs(ark_home):
    st = run.new_state(APPROVAL)
    assert st["status"] == "waiting" and st["jobs"] == {}
    assert st["plan_sha"] == "ab" * 32


def test_save_writes_state_and_run_file(ark_home):
    st = run.new_state(APPROVAL)
    st["status"] = "running"
    st["jobs"]["shell"] = run.new_job({"id": "shell", "tier": "T1"})
    run.event(st, "run started")
    run.save(st)
    assert run.load("demo", "notes-edit")["jobs"]["shell"]["status"] == "pending"
    md = paths.run_file("demo", "notes-edit").read_text()
    assert "Status: running" in md and "| shell | pending | T1 |" in md and "run started" in md
    assert desk._run("demo", "notes-edit") == "running"   # ark status reads the same line


def test_load_missing_is_none(ark_home):
    assert run.load("demo", "nope") is None


def test_events_are_capped(ark_home):
    st = run.new_state(APPROVAL)
    for i in range(250):
        run.event(st, f"e{i}")
    assert len(st["events"]) == run.MAX_EVENTS and st["events"][-1].endswith("e249")


import pytest


@pytest.mark.parametrize("content", ['{"repo": "r", "sl', "[1, 2]"])
def test_load_corrupt_raises_not_none(ark_home, content):
    f = run.state_file("r", "s")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    with pytest.raises(run.StateCorrupt, match="state.json"):
        run.load("r", "s")


def _planted(ark_home, name):
    """The tmp path of a save, as a link a worker planted there."""
    files = {"state": run.state_file("demo", "notes-edit"), "render": paths.run_file("demo", "notes-edit")}
    link = files[name].with_name(files[name].name + ".tmp")
    link.parent.mkdir(parents=True, exist_ok=True)
    return link, ark_home.parent / "victim"


@pytest.mark.parametrize("name", ["state", "render"])
def test_a_save_does_not_write_through_a_planted_tmp_link(ark_home, name):
    link, victim = _planted(ark_home, name)
    victim.write_text("mine")
    link.symlink_to(victim)
    run.save(run.new_state(APPROVAL))
    assert victim.read_text() == "mine"
    assert run.load("demo", "notes-edit")["slug"] == "notes-edit"


@pytest.mark.parametrize("name", ["state", "render"])
def test_a_save_does_not_create_the_target_of_a_dangling_tmp_link(ark_home, name):
    link, victim = _planted(ark_home, name)
    link.symlink_to(victim)
    run.save(run.new_state(APPROVAL))
    assert not victim.exists()


def test_a_tmp_link_to_a_read_only_file_does_not_stop_a_save(ark_home):
    link, victim = _planted(ark_home, "state")
    victim.write_text("mine")
    victim.chmod(0o444)
    link.symlink_to(victim)
    run.save(run.new_state(APPROVAL))
    assert victim.read_text() == "mine"
    assert run.load("demo", "notes-edit")["slug"] == "notes-edit"


def test_a_failed_write_leaves_no_tmp_behind(ark_home, monkeypatch):
    def refused(*a):
        raise OSError("disk says no")
    monkeypatch.setattr(run.os, "replace", refused)
    f = run.state_file("demo", "notes-edit")
    with pytest.raises(OSError, match="disk says no"):
        run._atomic(f, "text")
    assert list(f.parent.iterdir()) == []
