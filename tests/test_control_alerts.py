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
