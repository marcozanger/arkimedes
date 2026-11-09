"""An overlapping plan waits for an earlier finished run's open final PR."""
import json
from pathlib import Path

import pytest
from helpers import job, manifest

from ark import forge, gitops, loop, run
from test_loop import SLUG, _second_plan, drive, feature, run_finished, state, turn  # noqa: F401


def set_final_pr_state(repo, value):
    f = Path(repo) / ".git" / "ark-local-prs.json"
    prs = json.loads(f.read_text())
    url = state()["final_pr"]["url"]
    for p in prs:
        if p["url"] == url:
            p["state"] = value
    f.write_text(json.dumps(prs, indent=2))


def first_run_done(feature):
    repo, _ = feature()
    drive(run_finished)
    st = state()
    assert st["status"] == "done" and st["final_pr"]
    assert forge.LocalForge().view(repo, st["final_pr"]["number"])["state"] == "OPEN"
    return repo, st["final_pr"]["url"]


def waits_for(url):
    st = state(slug="second")
    return st["status"] == "waiting" and SLUG in st["reason"] and url in st["reason"]


@pytest.mark.parametrize("settled", ["MERGED", "CLOSED"])
def test_an_open_final_pr_holds_an_overlapping_plan_until_it_settles(feature, make_feature, settled):
    repo, url = first_run_done(feature)
    _second_plan(make_feature)
    alerts = [a for a in turn(5, 0) if "second" in a]
    assert waits_for(url) and gitops.remote_sha(repo, "ark/second") is None
    assert len(alerts) == 1 and alerts[0].startswith("demo/second: ") and url in alerts[0]
    set_final_pr_state(repo, settled)
    loop.cycle()
    st = state(slug="second")
    assert st["status"] == "running" and gitops.remote_sha(repo, "ark/second")
    assert url in st["settled_prs"]


def test_a_finished_run_without_a_final_pr_holds_nothing_back(feature, make_feature):
    repo, _ = first_run_done(feature)
    st = state()
    st["final_pr"] = None
    run.save(st)
    _second_plan(make_feature)
    loop.cycle()
    assert state(slug="second")["status"] == "running" and gitops.remote_sha(repo, "ark/second")


def test_a_finished_run_that_does_not_overlap_holds_nothing_back(feature, make_feature):
    repo, _ = first_run_done(feature)
    _second_plan(make_feature, manifest(jobs=[job("dbx", ["T1", "T2", "T3"], ["src/c/**"])]))
    loop.cycle()
    assert state(slug="second")["status"] == "running" and gitops.remote_sha(repo, "ark/second")


class Counting(forge.LocalForge):
    def __init__(self):
        self.calls, self.broken = [], False

    def view(self, repo, number):
        self.calls.append(number)
        if self.broken:
            raise forge.ForgeError("gh: could not resolve host")
        return super().view(repo, number)


def test_a_forge_error_waits_once_and_never_starts_on_an_unknown_state(feature, make_feature, monkeypatch):
    repo, url = first_run_done(feature)
    f = Counting()
    f.broken = True
    monkeypatch.setattr(forge, "get", lambda *a, **k: f)
    _second_plan(make_feature)
    alerts = [a for a in turn(5, 0) if "second" in a]
    st = state(slug="second")
    assert st["status"] == "waiting" and "cannot read the final PR" in st["reason"]
    assert gitops.remote_sha(repo, "ark/second") is None
    assert len(alerts) == 1 and "cannot read the final PR" in alerts[0]
    f.broken = False
    set_final_pr_state(repo, "MERGED")
    loop.cycle()
    assert state(slug="second")["status"] == "running"


def test_a_settled_final_pr_is_not_looked_up_again(feature, make_feature, monkeypatch):
    repo, url = first_run_done(feature)
    f = Counting()
    monkeypatch.setattr(forge, "get", lambda *a, **k: f)
    set_final_pr_state(repo, "MERGED")
    _second_plan(make_feature)
    loop.cycle()
    st = state(slug="second")
    assert st["status"] == "running" and url in st["settled_prs"]
    number = state()["final_pr"]["number"]
    assert f.calls.count(number) == 1
    again = dict(st, status="waiting", reason="", jobs={})  # a waiting plan that already settled it
    loop.try_start(again, [state(), again], f)
    assert f.calls.count(number) == 1
