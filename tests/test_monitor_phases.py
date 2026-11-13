import time
from datetime import datetime, timedelta, timezone

from ark import monitor, proc
from test_monitor_runs import iso, make_run


def _procs(monkeypatch, name, ago_s):
    monkeypatch.setattr(proc, "job_processes", lambda d, job: [{"name": f"{job}.{name}", "start": time.time() - ago_s}])


def test_a_job_with_a_worker_is_developing(make_feature, monkeypatch):
    now = datetime.now(timezone.utc)
    make_run(make_feature, jobs={"shell": {"status": "working", "started": iso(now - timedelta(minutes=5))}})
    _procs(monkeypatch, "worker", 120)
    [p] = monitor.build(now)["plans"]
    ph = p["jobs"][0]["phase"]
    assert ph["kind"] == "developing" and ph["usual_min"] is None and ph["since"]


def test_a_job_running_its_gate_is_testing(make_feature, monkeypatch):
    now = datetime.now(timezone.utc)
    make_run(make_feature, jobs={"shell": {"status": "gating", "started": iso(now - timedelta(minutes=5))}})
    _procs(monkeypatch, "gate", 60)
    [p] = monitor.build(now)["plans"]
    assert p["jobs"][0]["phase"]["kind"] == "testing"


def test_a_merged_job_has_no_phase(make_feature, monkeypatch):
    now = datetime.now(timezone.utc)
    make_run(make_feature, jobs={"shell": {"status": "merged", "started": iso(now), "finished": iso(now)}})
    _procs(monkeypatch, "worker", 5)
    [p] = monitor.build(now)["plans"]
    assert p["jobs"][0]["phase"] is None
