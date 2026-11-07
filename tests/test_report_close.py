import json

from control_helpers import SLUG, drive, state


def alerts():
    """What the run alerted: a tick moves st["alerts"] into runs/alerts.jsonl."""
    from ark import paths
    f = paths.runs_dir() / "alerts.jsonl"
    return [json.loads(x)["text"] for x in f.read_text().splitlines()] if f.exists() else []


def finished():
    st = state()
    return bool(st) and st.get("closing") == "done"


def test_a_closed_run_has_its_report_history_and_pr_summary(feature):
    from ark import forge, history, paths
    repo, _ = feature()
    drive(finished)
    st = state()
    d = paths.run_dir("demo", SLUG)
    assert st["status"] == "done" and st["report"] == str(d / "report.html")
    assert (d / "report.html").exists()
    rows = [json.loads(x) for x in (d / "steps.jsonl").read_text().splitlines()]
    kinds = {r["kind"] for r in rows}
    assert {"worker", "review", "gate", "integrate", "merge", "decisions"} <= kinds
    assert sum(r["kind"] == "merge" for r in rows) == len(st["jobs"])
    body = forge.LocalForge().list(repo)[-1]["body"]
    assert body.startswith(f"**Decisions:** `docs/decisions/{SLUG}.md`") and "\n## Report\n" in body
    assert any(a.endswith(f"· report: {d / 'report.html'}") for a in alerts())
    jobs = (history.history_dir() / "jobs.jsonl").read_text().splitlines()
    assert len(jobs) == len(st["jobs"])
    assert (history.history_dir() / "insights.md").exists()


def test_a_report_that_fails_never_stops_the_close(feature, monkeypatch):
    from ark import forge, reporting
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(reporting, "generate", boom)
    repo, _ = feature()
    drive(finished)
    st = state()
    assert st["status"] == "done" and "report" not in st
    assert forge.LocalForge().list(repo)[-1]["body"].startswith("**Decisions:**")
    assert any("report not written: RuntimeError: disk full" in e for e in st["events"])
    assert any(a.endswith("run done: final PR " + st["final_pr"]["url"]) for a in alerts())
