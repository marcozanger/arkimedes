import json
from datetime import datetime, timedelta, timezone

import pytest

from ark import paths, sweep

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def state(status="blocked", closing="done", jobs=None, closed=None, events=None, repo="demo", slug="s"):
    """A state with the fields run.new_state fills, written under runs/<repo>/<slug>/state.json."""
    st = {"repo": repo, "slug": slug, "plan": "p", "plan_sha": "0" * 64, "approved_at": "2026-11-01T00:00:00+00:00",
          "status": status, "reason": "", "default_branch": "master", "base_sha": None,
          "started": "2026-11-01T00:00:00+00:00", "updated": "2026-11-01T00:00:00+00:00", "closing": closing,
          "final_pr": None, "events": list(events or []), "alerts": [], "times_kept": True,
          "jobs": {k: {"status": v} for k, v in (jobs or {"a": "merged"}).items()}}
    if closed is not None:
        st["closed_at"] = closed.isoformat()
    f = paths.runs_dir() / repo / slug / "state.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st))
    return st


def set_sweep(ark_home, block):
    ws = json.loads((ark_home / "workspace.json").read_text())
    ws["sweep"] = block
    (ark_home / "workspace.json").write_text(json.dumps(ws))


def test_settings_defaults(ark_home):
    assert paths.load_workspace()["sweep"] == {}
    assert sweep.settings() == {"enabled": True, "abandon_days": 14}


def test_settings_each_key_alone(ark_home):
    set_sweep(ark_home, {"enabled": False})
    assert sweep.settings() == {"enabled": False, "abandon_days": 14}
    set_sweep(ark_home, {"abandon_days": 3})
    assert sweep.settings() == {"enabled": True, "abandon_days": 3}


@pytest.mark.parametrize("block,key", [({"enabled": "no"}, "sweep.enabled"), ({"enabled": 1}, "sweep.enabled"),
                                       ({"abandon_days": 0}, "sweep.abandon_days"),
                                       ({"abandon_days": True}, "sweep.abandon_days"),
                                       ({"abandon_days": 2.5}, "sweep.abandon_days"),
                                       ({"days": 3}, "sweep.days")])
def test_settings_refuse_bad_values(ark_home, block, key):
    set_sweep(ark_home, block)
    with pytest.raises(ValueError, match=key):
        sweep.settings()


def test_closed_at_sources(ark_home):
    t = NOW - timedelta(days=2)
    assert sweep.closed_at(state(closed=t, events=["2020-01-01T00:00:00+00:00 · x"])) == t
    assert sweep.closed_at(state(events=["2026-11-01T00:00:00+00:00 · a", "2026-11-02T03:00:00+00:00 · run blocked"])) \
        == datetime(2026, 11, 2, 3, tzinfo=timezone.utc)
    assert sweep.closed_at(state(events=[])) is None


def test_classify_table(ark_home):
    t = NOW - timedelta(days=20)
    assert sweep.classify(state("running", None, closed=t), NOW) == ("live", None)
    assert sweep.classify(state("waiting", None), NOW) == ("live", None)
    assert sweep.classify(state("blocked", "publish", closed=t), NOW) == ("live", None)
    assert sweep.classify(state("aborted", closed=t), NOW) == ("aborted", t)
    assert sweep.classify(state("done", closed=t), NOW) == ("done", t)
    assert sweep.classify(state("done", jobs={"a": "merged", "b": "blocked"}, closed=t), NOW) \
        == ("abandoned", t + timedelta(days=14))
    assert sweep.classify(state("blocked", closed=t), NOW) == ("abandoned", t + timedelta(days=14))
    recent = NOW - timedelta(days=3)
    assert sweep.classify(state("blocked", closed=recent), NOW) == ("window", recent + timedelta(days=14))
    assert sweep.classify(state("blocked", events=[]), NOW) == ("window", None)


def test_classify_follows_abandon_days(ark_home):
    set_sweep(ark_home, {"abandon_days": 2})
    t = NOW - timedelta(days=3)
    assert sweep.classify(state("blocked", closed=t), NOW) == ("abandoned", t + timedelta(days=2))


def test_fourteen_day_boundary(ark_home):
    almost = NOW - timedelta(days=13, hours=23)
    assert sweep.classify(state("blocked", closed=almost), NOW)[0] == "window"
    exact = NOW - timedelta(days=14)
    assert sweep.classify(state("blocked", closed=exact), NOW) == ("abandoned", NOW)


def test_abandoned(ark_home):
    t = NOW - timedelta(days=15)
    assert sweep.abandoned(state("blocked", closed=t), NOW) == t + timedelta(days=14)
    assert sweep.abandoned(state("blocked", closed=NOW - timedelta(days=1)), NOW) is None
    assert sweep.abandoned(state("aborted", closed=t), NOW) is None
    assert sweep.abandoned(state("running", None), NOW) is None
