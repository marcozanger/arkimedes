"""The daily sweep inside ark-loop's cycle: once per local day, its alerts said once."""
import json

import pytest

from ark import forge, loop, paths, proc, sweep
from test_sweep_plan import make_run, wt_of

DAY1, DAY2, DAY3, DAY4, DAY5 = "2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"


@pytest.fixture
def repo(runner, monkeypatch):
    loop._said.clear()
    monkeypatch.setattr(sweep, "today", lambda: DAY1)
    return runner("demo")


def on(monkeypatch, day):
    monkeypatch.setattr(sweep, "today", lambda: day)


def sweep_file():
    return paths.runs_dir() / "sweep.json"


def set_workspace(**kv):
    f = paths.ark_home() / "workspace.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), **kv}))


def sweeps(alerts):
    return [a for a in alerts if a.startswith("sweep:")]


def test_the_first_cycle_of_a_day_sweeps_and_the_next_one_does_not(repo, monkeypatch):
    make_run(repo, "gone")
    loop.cycle()
    assert not wt_of("demo", "gone", "a").exists()
    assert json.loads(sweep_file().read_text())["day"] == DAY1
    make_run(repo, "later")
    loop.cycle()
    assert wt_of("demo", "later", "a").exists()
    on(monkeypatch, DAY2)
    loop.cycle()
    assert not wt_of("demo", "later", "a").exists()
    assert json.loads(sweep_file().read_text())["day"] == DAY2


def test_a_disabled_sweep_does_nothing(repo):
    set_workspace(sweep={"enabled": False})
    make_run(repo, "gone")
    loop.cycle()
    assert wt_of("demo", "gone", "a").exists()
    assert not sweep_file().exists()


def test_a_bad_sweep_setting_stops_the_cycle_before_any_process(ark_home, monkeypatch):
    set_workspace(sweep={"enabled": "yes"})
    monkeypatch.setattr(proc, "start", lambda *a, **k: pytest.fail("a process started"))
    with pytest.raises(ValueError, match="sweep.enabled"):
        loop.cycle(forge.get())


def fake_sweep(monkeypatch, held=(), errors=()):
    """plan() returns one run whose `held` and failing items are what the test says today."""
    box = {"held": list(held), "errors": list(errors)}

    def plan(now, f):
        rp = sweep.RunPlan("demo", "old", "aborted")
        rp.held = [(sweep.Item("worktree", t, "demo", "old"), why) for t, why in box["held"]]
        rp.items = [sweep.Item("local_branch", t, "demo", "old") for t, _ in box["errors"]]
        return [rp], []

    def apply(plans, f, now):
        errs = [(sweep.Item("local_branch", t, "demo", "old").key(), msg) for t, msg in box["errors"]]
        return {"removed": [], "errors": errs, "held": []}
    monkeypatch.setattr(sweep, "plan", plan)
    monkeypatch.setattr(sweep, "apply", apply)
    return box


def test_a_held_item_alerts_once_while_it_stays_held(repo, monkeypatch):
    box = fake_sweep(monkeypatch, held=[("/wt/demo/old/a", "uncommitted changes")])
    text = "sweep: kept /wt/demo/old/a of demo/old: uncommitted changes"
    assert sweeps(loop.cycle()[1]) == [text]
    on(monkeypatch, DAY2)
    assert sweeps(loop.cycle()[1]) == []
    box["held"] = []
    on(monkeypatch, DAY3)
    assert sweeps(loop.cycle()[1]) == []
    box["held"] = [("/wt/demo/old/a", "uncommitted changes")]
    on(monkeypatch, DAY4)
    assert sweeps(loop.cycle()[1]) == [text]


def test_an_error_alerts_once_on_its_third_day_in_a_row(repo, monkeypatch):
    box = fake_sweep(monkeypatch, errors=[("ark/old-a", "git broke")])
    text = "sweep: ark/old-a failed 3 days in a row: git broke"
    said = []
    for day in (DAY1, DAY2, DAY3, DAY4):
        on(monkeypatch, day)
        said.append(sweeps(loop.cycle()[1]))
    assert said == [[], [], [text], []]
    box["errors"] = []
    on(monkeypatch, DAY5)
    loop.cycle()
    assert json.loads(sweep_file().read_text())["errors"] == {}


def test_an_error_that_stops_for_a_day_counts_again_from_one(repo, monkeypatch):
    box = fake_sweep(monkeypatch)
    said = []
    for n, fails in enumerate((True, False, True, True, True)):
        box["errors"] = [("ark/old-a", "git broke")] if fails else []
        on(monkeypatch, f"2026-12-0{n + 1}")
        said.append(sweeps(loop.cycle()[1]))
    assert said == [[], [], [], [], ["sweep: ark/old-a failed 3 days in a row: git broke"]]


def test_a_sweep_that_raises_is_one_alert_and_the_cycle_goes_on(repo, monkeypatch):
    def boom(now, f):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(sweep, "plan", boom)
    line, alerts, _ = loop.cycle()
    assert isinstance(line, str)
    assert [a for a in alerts if "disk on fire" in a] == ["sweep: RuntimeError: disk on fire"]
    assert not [a for a in loop.cycle()[1] if "disk on fire" in a]
