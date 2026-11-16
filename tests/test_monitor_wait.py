from ark import monitor, run
from test_monitor_runs import make_run

HOLD = "2026-11-16T16:27:27+00:00 · refresh: a gate waits for the machine (load 12.2 above 12)"
DONE = "2026-11-16T16:47:37+00:00 · refresh 0: main@3b3e7776 merged into ark/x is green"


def _events(events):
    st = run.load("demo", "notes-edit")
    st["events"] = events
    run.save(st)


def test_a_run_held_by_the_load_says_so(make_feature):
    make_run(make_feature)
    _events([HOLD])
    [p] = monitor.build()["plans"]
    w = p["wait"]
    assert w["kind"] == "machine" and w["load"] == 12.2 and w["limit"] == 12.0 and "load 12.2" in w["text"]
    assert p["jobs"][0]["wait"]["kind"] == "machine"
    assert p["jobs"][1]["wait"]["kind"] == "dependency" and "shell" in p["jobs"][1]["wait"]["text"]


def test_the_hold_ends_with_a_later_event_of_the_same_step(make_feature):
    make_run(make_feature)
    _events([HOLD, DONE])
    [p] = monitor.build()["plans"]
    assert p["wait"] is None and p["jobs"][0]["wait"]["kind"] == "queue"


def test_a_finished_run_waits_for_nothing(make_feature):
    make_run(make_feature, "done")
    _events([HOLD])
    [p] = monitor.build()["plans"]
    assert p["wait"] is None and all(j["wait"] is None for j in p["jobs"])


def test_the_feed_carries_the_machine_load(ark_home, monkeypatch):
    monkeypatch.setattr("os.getloadavg", lambda: (17.84, 12.6, 10.3))
    m = monitor.build()["machine"]
    assert m["load"] == [17.8, 12.6, 10.3] and m["cores"] >= 1 and m["limit"] > 0
