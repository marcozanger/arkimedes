import json
from datetime import datetime, timedelta, timezone

from control_helpers import ark
from helpers import approve, plan_text

from ark import beat, monitor, paths, run


def make_run(make_feature, status="running", jobs=None, slug="notes-edit", with_state=True):
    p = make_feature(plan_text(slug=slug), slug=slug)
    a = json.loads(approve(p, slug=slug).read_text())
    if with_state:
        st = run.new_state(a)
        st["status"] = status
        for jid, fields in (jobs or {}).items():
            st["jobs"][jid] = {**run.new_job({"tier": "T1"}), **fields}
        run.save(st)
    return p


def iso(dt):
    return dt.isoformat(timespec="seconds")


def test_a_running_plan(make_feature):
    now = datetime.now(timezone.utc)
    make_run(make_feature, jobs={"shell": {"status": "working", "started": iso(now - timedelta(minutes=40))}})
    out = monitor.build(now)
    assert out["version"] == 1 and out["generated"]
    [p] = out["plans"]
    assert (p["repo"], p["slug"], p["status"], p["approval"], p["error"]) == ("demo", "notes-edit", "running", "ok", None)
    assert [j["id"] for j in p["jobs"]] == ["shell", "ui"] and p["jobs"][1]["status"] == "pending"
    assert p["progress"] == {"merged": 0, "total": 2}
    assert (p["times"]["delay_min"], p["times"]["left_min"]) == (10, 30)
    shell = p["jobs"][0]
    assert shell["pr_title"] == "feat: shell" and shell["budget"] == {"minutes": 30, "usd": 2}
    assert shell["tasks"] == ["T1 — step", "T2 — step"] and p["jobs"][1]["after"] == ["shell"]
    assert shell["plan_start"] and shell["fc_end"]
    assert p["title"] == "Notes edit" and "## Run manifest" in p["plan_file"]
    assert p["manifest"]["final_pr_title"] == "feat(notes): edit and delete notes"


def test_an_approved_plan_with_no_run_yet_is_waiting(make_feature):
    make_run(make_feature, with_state=False)
    [p] = monitor.build()["plans"]
    assert p["status"] == "waiting" and p["times"]["left_min"] == 60 and p["error"] is None


def test_held_blocked_and_done(make_feature):
    t = iso(datetime.now(timezone.utc))
    make_run(make_feature, "held", slug="a-held")
    make_run(make_feature, "blocked", {"shell": {"status": "blocked", "started": t, "finished": t}}, slug="b-blocked")
    make_run(make_feature, "done", {"shell": {"status": "merged", "started": t, "finished": t},
                                    "ui": {"status": "merged", "started": t, "finished": t}}, slug="c-done")
    held, blocked, done = monitor.build()["plans"]
    assert held["times"]["left_min"] is None and blocked["times"]["left_min"] is None
    assert done["times"]["left_min"] == 0 and done["progress"] == {"merged": 2, "total": 2}


def test_a_plan_edited_after_approval_is_stale(make_feature):
    p = make_run(make_feature)
    p.write_text(p.read_text() + "\nedited\n")
    assert monitor.build()["plans"][0]["approval"] == "stale"


def test_a_corrupt_state_breaks_only_its_plan(make_feature):
    make_run(make_feature, slug="a-bad")
    make_run(make_feature, slug="b-good")
    run.state_file("demo", "a-bad").write_text("{")
    bad, good = monitor.build()["plans"]
    assert bad["error"] and "corrupt" in bad["error"] and bad["jobs"] == []
    assert good["error"] is None and good["status"] == "running"


def test_alerts_have_ids_and_old_lines_read_as_empty(ark_home):
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    (paths.runs_dir() / "alerts.jsonl").write_text(
        json.dumps({"at": "t1", "text": "old", "critical": False}) + "\n"
        + "not json\n"
        + json.dumps({"at": "t3", "text": "new", "critical": True, "repo": "demo", "slug": "s", "job": "j"}) + "\n")
    assert monitor.build()["alerts"] == [
        {"id": 1, "at": "t1", "text": "old", "critical": False, "repo": "", "slug": "", "job": ""},
        {"id": 3, "at": "t3", "text": "new", "critical": True, "repo": "demo", "slug": "s", "job": "j"}]


def test_no_alerts_file_is_no_alerts(ark_home):
    assert monitor.build()["alerts"] == []


def test_only_the_last_alerts_are_sent(ark_home):
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    (paths.runs_dir() / "alerts.jsonl").write_text(
        "".join(json.dumps({"at": "t", "text": str(i)}) + "\n" for i in range(monitor.MAX_ALERTS + 5)))
    got = monitor.build()["alerts"]
    assert len(got) == monitor.MAX_ALERTS and got[-1]["id"] == monitor.MAX_ALERTS + 5


def test_the_loop_is_read_from_its_beat(ark_home):
    assert monitor.build()["loop"]["alive"] is False
    beat.write(15)
    assert monitor.build()["loop"]["alive"] is True


def test_the_decisions_file_is_sent_verbatim(make_feature):
    make_run(make_feature)
    (paths.run_dir("demo", "notes-edit") / "decisions.md").write_text("# Decisions\n\n- D1\n")
    assert monitor.build()["plans"][0]["decisions"] == "# Decisions\n\n- D1\n"


def test_the_command(make_feature, capsys):
    make_run(make_feature)
    code, out = ark(capsys, "runs", "--json")
    assert code == 0 and json.loads(out)["plans"][0]["slug"] == "notes-edit"
    code, out = ark(capsys, "runs")
    assert code == 2 and "--json" in out


def test_nothing_is_written(make_feature):
    make_run(make_feature)
    before = sorted((p, p.stat().st_mtime_ns) for p in paths.ark_home().rglob("*") if p.is_file())
    monitor.build()
    after = sorted((p, p.stat().st_mtime_ns) for p in paths.ark_home().rglob("*") if p.is_file())
    assert before == after
