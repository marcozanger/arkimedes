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
