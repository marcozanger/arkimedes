import json

from ark import notify, paths, run


def lines():
    return [json.loads(x) for x in (paths.runs_dir() / "alerts.jsonl").read_text().splitlines()]


def configure(ark_home, command):
    f = ark_home / "workspace.json"
    ws = json.loads(f.read_text())
    ws["notify"] = command
    f.write_text(json.dumps(ws))


def test_a_critical_alert_is_also_kept_apart():
    st = {"repo": "r", "slug": "s", "alerts": [], "events": [], "updated": ""}
    run.alert(st, "plain")
    run.alert(st, "urgent", critical=True)
    assert st["alerts"] == ["r/s: plain", "r/s: urgent"]
    assert st["critical"] == ["r/s: urgent"]


def test_the_default_notifier_is_none(ark_home):
    assert paths.load_workspace()["notify"] == []


def test_every_alert_is_recorded_and_only_critical_ones_are_sent(ark_home, tmp_path):
    sent = tmp_path / "sent"
    configure(ark_home, ["sh", "-c", f'printf "%s\\n" "$1" >> "{sent}"', "notify"])
    assert notify.deliver(["a", "b"], ["b"]) is None
    assert sent.read_text() == "b\n"
    assert [(x["text"], x["critical"]) for x in lines()] == [("a", False), ("b", True)]


def test_a_long_burst_sends_a_few_and_says_how_many_more(ark_home, tmp_path):
    sent = tmp_path / "sent"
    configure(ark_home, ["sh", "-c", f'printf "%s\\n" "$1" >> "{sent}"', "notify"])
    many = [f"x{i}" for i in range(notify.MAX_PER_CYCLE + 3)]
    notify.deliver(many, many)
    got = sent.read_text().splitlines()
    assert got[:notify.MAX_PER_CYCLE - 1] == many[:notify.MAX_PER_CYCLE - 1]
    assert len(got) == notify.MAX_PER_CYCLE and "4 alerts more" in got[-1]


def test_a_broken_notifier_never_raises_and_says_why(ark_home):
    configure(ark_home, ["/no/such/program"])
    assert "/no/such/program" in notify.deliver(["a"], ["a"])
    configure(ark_home, ["false"])
    assert notify.deliver(["a"], ["a"])
    configure(ark_home, "not a list")
    assert "notify" in notify.deliver(["a"], ["a"])


def test_nothing_to_say_writes_nothing(ark_home):
    assert notify.deliver([], []) is None
    assert not (paths.runs_dir() / "alerts.jsonl").exists()


from control_helpers import SLUG, drive, settled, state
from helpers import job, manifest

from ark import loop, proc


def sent_file(ark_home, tmp_path):
    sent = tmp_path / "sent"
    configure(ark_home, ["sh", "-c", f'printf "%s\\n" "$1" >> "{sent}"', "notify"])
    return sent


def test_the_end_of_a_run_reaches_the_notifier(feature, ark_home, tmp_path):
    sent = sent_file(ark_home, tmp_path)
    feature()
    drive(lambda: (state() or {}).get("status") == "done")
    assert f"demo/{SLUG}: run done" in sent.read_text()
    assert "critical" not in state()
    assert any(x["critical"] and "run done" in x["text"] for x in lines())


def test_an_unreachable_model_reaches_the_notifier(feature, ark_home, tmp_path, monkeypatch):
    sent = sent_file(ark_home, tmp_path)
    monkeypatch.setenv("FAKE_DOWN", "1")
    monkeypatch.setattr(loop, "BACKOFF_FIRST", 0)
    monkeypatch.setattr(loop, "PASSING_WAITS", (0,) * 5)
    feature()
    drive(lambda: "unreachable" in (sent.read_text() if sent.exists() else ""))


def test_a_plain_alert_does_not_reach_the_notifier(feature, ark_home, tmp_path, monkeypatch):
    sent = sent_file(ark_home, tmp_path)
    feature()
    st = None
    while st is None:
        loop.cycle()
        st = state()
    run.alert(st, "only for the terminal")
    run.save(st)
    drive(lambda: state()["status"] == "done")
    assert "only for the terminal" not in sent.read_text()
    assert any("only for the terminal" in x["text"] and not x["critical"] for x in lines())


def test_a_broken_notifier_is_said_once_and_the_run_goes_on(feature, ark_home):
    configure(ark_home, ["/no/such/program"])
    feature()
    said = []
    for _ in range(400):
        said += loop.cycle()[1]
        if state() and state()["status"] == "done":
            break
    assert state()["status"] == "done"
    assert len([a for a in said if "notifier failed" in a]) == 1


def live_state(repo, slug, status="running", **jobs):
    return {"repo": repo, "slug": slug, "status": status, "jobs": {k: {"status": v} for k, v in jobs.items()}}


def test_nothing_to_advance(ark_home):
    held = live_state("r", "a", "held", x="needs_worker")
    answer = live_state("r", "b", x="waiting_answer", y="pending")
    attached = live_state("r", "c", x="attached")
    moving = live_state("r", "d", x="waiting_answer", y="needs_push")
    finished = live_state("r", "e", "done", x="merged")
    assert loop._nothing_to_advance([held, answer, attached, finished])
    assert not loop._nothing_to_advance([held, moving])
    assert not loop._nothing_to_advance([live_state("r", "f", x="pending")])
    assert not loop._nothing_to_advance([finished])
    assert not loop._nothing_to_advance([])


def test_nothing_to_advance_is_false_while_a_process_lives(ark_home):
    d = paths.run_dir("r", "a")
    d.mkdir(parents=True)
    (d / "x.worker.proc").write_text("{}")
    assert not loop._nothing_to_advance([live_state("r", "a", "held", x="needs_worker")])


def test_a_stuck_workspace_is_said_once_per_spell(ark_home):
    states = [live_state("r", "a", "held", x="needs_worker")]
    alerts, critical = [], []
    loop._flag_stuck(states, False, alerts, critical)
    loop._flag_stuck(states, False, alerts, critical)
    assert len(alerts) == 1 and critical == alerts and "nothing left to advance" in alerts[0]
    loop._flag_stuck([], False, alerts, critical)
    loop._flag_stuck(states, False, alerts, critical)
    assert len(alerts) == 2
