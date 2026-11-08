import json
from datetime import datetime, timedelta, timezone

import pytest

from ark import desk, paths, run
from helpers import approve, job, manifest, plan_text

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def iso(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def mf(*jobs):
    return {"jobs": list(jobs)}


def j(id, minutes, after=()):
    return job(id, ["T1"], ["a/**"], after=after, budget={"minutes": minutes, "usd": 1}) if minutes is not None \
        else job(id, ["T1"], ["a/**"], after=after, budget={})


def st(status="running", started=72, updated=0, **jobs):
    return {"status": status, "started": iso(started), "updated": iso(updated),
            "jobs": {k: {"status": v} for k, v in jobs.items()}}


@pytest.mark.parametrize("m,txt", [(0, "0m"), (30, "30m"), (59.6, "1h00m"), (72, "1h12m"), (185, "3h05m")])
def test_fmt_minutes(m, txt):
    assert desk.fmt_minutes(m) == txt


def test_elapsed_running_uses_now():
    assert desk._elapsed(st(started=72), NOW) == "1h12m"


def test_elapsed_finished_is_frozen():
    assert desk._elapsed(st("done", started=300, updated=115), NOW) == "3h05m"


def test_elapsed_missing_started_or_state():
    s = st()
    del s["started"]
    assert desk._elapsed(s, NOW) == "-"
    assert desk._elapsed(None, NOW) == "-"


def test_eta_parallel_unmerged():
    m = mf(j("A", 20), j("B", 30))
    assert desk._eta(st(A="pending", B="pending"), m, 3) == "~30m"


def test_eta_drops_merged_job():
    m = mf(j("A", 20), j("B", 30, after=["A"]))
    assert desk._eta(st(A="merged", B="pending"), m, 3) == "~30m"


def test_eta_chain():
    m = mf(j("A", 20), j("B", 30, after=["A"]))
    assert desk._eta(st(A="pending", B="pending"), m, 3) == "~50m"


def test_eta_waiting_counts_and_done_does_not():
    m = mf(j("A", 20))
    assert desk._eta(st("waiting", A="pending"), m, 3) == "~20m"
    assert desk._eta(st("done", A="pending"), m, 3) == "-"
    assert desk._eta(None, m, 3) == "-"


def test_eta_unknown_budget():
    assert desk._eta(st(A="pending", B="pending"), mf(j("A", 20), j("B", None)), 3) == "?"


def test_eta_everything_merged():
    assert desk._eta(st(A="merged"), mf(j("A", 20)), 3) == "-"


def test_features_no_run(ark_home, make_feature):
    make_feature()
    f = desk.features(now=NOW)[0]
    assert (f.elapsed, f.eta) == ("-", "-")


def test_features_running(ark_home, make_feature):
    p = make_feature(plan_text(manifest(jobs=[j("A", 20), j("B", 30)])))
    approve(p)
    s = run.new_state(json.loads(paths.approval_file("demo", "notes-edit").read_text()))
    s.update(status="running", started=iso(72), updated=iso(1))
    s["jobs"] = {"A": run.new_job({"id": "A", "tier": "T1"}), "B": run.new_job({"id": "B", "tier": "T1"})}
    run.save(s)
    f = desk.features(now=NOW)[0]
    assert (f.elapsed, f.eta) == ("1h12m", "~30m")
