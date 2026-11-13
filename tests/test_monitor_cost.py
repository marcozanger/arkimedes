import json
from datetime import datetime, timedelta, timezone

from ark import euklides, history, monitor
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


def test_a_closed_plan_carries_its_close_date_and_tokens(make_feature):
    t = iso(datetime.now(timezone.utc))
    make_run(make_feature, "done", {"shell": {"status": "merged", "started": t, "finished": t},
                                    "ui": {"status": "merged", "started": t, "finished": t}})
    write_rows({"plan": "notes-edit", "repo": "demo", "usd": 7.5, "ended": "2026-11-07T03:55:00+00:00", "tokens": 884000})
    c = monitor.build()["plans"][0]["cost"]
    assert (c["closed_at"], c["tokens"]) == ("2026-11-07T03:55:00+00:00", 884000)


def test_a_live_plan_has_no_close_date_or_tokens(make_feature):
    make_run(make_feature)
    c = monitor.build()["plans"][0]["cost"]
    assert (c["closed_at"], c["tokens"]) == (None, None)


def test_a_plan_has_its_account_and_euklides_decisions(make_feature):
    make_run(make_feature)
    assert monitor.build()["plans"][0]["euklides"] == {"decisions": 0, "asked": 0}
    euklides.folder().mkdir(parents=True, exist_ok=True)
    rows = [{"id": "demo/notes-edit/shell/1", "repo": "demo", "slug": "notes-edit", "action": "ask"},
            {"id": "demo/notes-edit/shell/2", "repo": "demo", "slug": "notes-edit", "action": "raise"},
            {"id": "demo/other/ui/1", "repo": "demo", "slug": "other", "action": "ask"}]
    (euklides.folder() / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    [p] = monitor.build()["plans"]
    assert p["euklides"] == {"decisions": 2, "asked": 1}
    assert p["account"] == "personal"
