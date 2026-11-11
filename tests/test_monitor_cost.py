import json
from datetime import datetime, timedelta, timezone

from ark import history, monitor
from test_monitor_runs import iso, make_run


def write_rows(*rows):
    history.history_dir().mkdir(parents=True, exist_ok=True)
    (history.history_dir() / "plans.jsonl").write_text("".join(
        (r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))


def test_a_live_plan_sums_usd_and_budget(make_feature):
    now = datetime.now(timezone.utc)
    make_run(make_feature, jobs={"shell": {"status": "working", "usd": 1.25, "started": iso(now - timedelta(minutes=5))}})
    [p] = monitor.build(now)["plans"]
    c = p["cost"]
    assert (c["usd"], c["budget_usd"], c["closed"]) == (1.25, 4, False)
    assert c["working_min"] is None and c["estimated_min"] == 60


def test_a_closed_plan_reads_its_history_row(make_feature):
    t = iso(datetime.now(timezone.utc))
    make_run(make_feature, "done", {"shell": {"status": "merged", "started": t, "finished": t},
                                    "ui": {"status": "merged", "started": t, "finished": t}})
    write_rows({"plan": "notes-edit", "repo": "demo", "usd": 7.5, "budget_usd": 4, "wall_minutes": 558.4,
                "working_minutes": 104.6, "waiting_minutes": 450.2})
    c = monitor.build()["plans"][0]["cost"]
    assert (c["usd"], c["budget_usd"], c["closed"]) == (7.5, 4, True)
    assert (c["wall_min"], c["working_min"], c["waiting_min"]) == (558, 105, 450)


def test_a_broken_row_is_ignored(make_feature):
    t = iso(datetime.now(timezone.utc))
    make_run(make_feature, "blocked", {"shell": {"status": "blocked", "started": t, "finished": t}})
    write_rows({"plan": "notes-edit", "repo": "demo", "working_minutes": "x"}, "not json")
    [p] = monitor.build()["plans"]
    assert p["error"] is None and p["cost"]["working_min"] is None
