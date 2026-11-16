import json

from ark import monitor, paths, phases
from test_monitor_runs import make_run


def _steps(repo, slug, rows):
    d = paths.runs_dir() / repo / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "steps.jsonl").write_text("\n".join(json.dumps({"minutes": m, "kind": k}) for k, m in rows) + "\n")


def test_shares_come_from_the_repos_earlier_steps(ark_home):
    _steps("demo", "old", [("worker", 60), ("euklides", 20), ("gate", 10), ("integrate", 10), ("review", 20),
                           ("wait-marco", 999), ("merge", 0)])
    sh = phases.shares("demo")
    # 80 developing, 20 testing, 20 reviewing of 120 minutes: the 999 min waiting for Marco are not work
    assert sh["basis"] == "demo" and sh["share"] == {"developing": 0.667, "testing": 0.167, "reviewing": 0.167, "other": 0.0}


def test_a_repo_with_little_history_borrows_every_repos(ark_home):
    _steps("demo", "old", [("worker", 5)])
    _steps("other", "old", [("worker", 70), ("gate", 30)])
    assert phases.shares("demo")["basis"] == "all repos"


def test_no_history_no_estimate(make_feature):
    make_run(make_feature)
    [p] = monitor.build()["plans"]
    assert p["estimate"] is None and p["jobs"][0]["estimate"] is None


def test_a_plan_estimate_is_its_budget_cut_by_the_shares(make_feature):
    make_run(make_feature)
    _steps("demo", "old", [("worker", 50), ("gate", 25), ("review", 25)])
    [p] = monitor.build()["plans"]
    assert p["jobs"][0]["estimate"] == {"developing": 15.0, "testing": 7.5, "reviewing": 7.5, "other": 0.0}
    assert round(sum(p["estimate"]["minutes"].values())) == 60 and p["estimate"]["basis"] == "demo"
