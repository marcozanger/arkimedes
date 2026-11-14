"""The run's base follows main: a check before any job, a refresh each time main moves, a red main holds."""
from conftest import git
from helpers import manifest
from test_loop import SLUG, drive, feature, jobs_settled, state  # noqa: F401  (test_loop's own feature fixture)

import pytest

from ark import gitops, loop, paths


@pytest.fixture(autouse=True)
def start_check(monkeypatch):
    """conftest turns the start check off for the suite; these tests are about it."""
    monkeypatch.setenv("ARK_START_CHECK", "1")


def commit_on(repo, branch, rel, text) -> str:
    """Commit `rel` on `branch` (from origin's tip) and push it; master stays checked out. The new sha."""
    git("fetch", "-q", "origin", cwd=repo)
    git("checkout", "-q", "-B", branch, f"origin/{branch}", cwd=repo)
    (repo / rel).write_text(text)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", f"change {rel}", cwd=repo)
    git("push", "-q", "origin", branch, cwd=repo)
    sha = gitops.rev(repo, "HEAD")
    if branch != "master":
        git("checkout", "-q", "master", cwd=repo)
    return sha


def alerts(st) -> list[str]:
    """Every alert the run raised: a cycle hands st["alerts"] to the terminal and empties it; the events keep them."""
    return [e for e in st["events"] if " · ALERT " in e]


# ---- T5: the start check ----------------------------------------------------------------------

def test_a_run_dispatches_no_job_before_its_base_is_checked(feature, monkeypatch):
    feature(manifest(integration_gate="true"))
    loop.cycle()
    st = state()
    assert st["refresh"]["n"] == 0 and st["base_green"] is False
    assert all(j["status"] == "pending" for j in st["jobs"].values())
    drive(lambda: state()["base_green"])
    assert state()["refreshes"][0]["result"] == "landed"
    drive(jobs_settled)


def test_a_red_main_at_start_holds_every_job_and_alerts_once(feature):
    repo, _ = feature(manifest(integration_gate="test ! -e bad.txt"))
    commit_on(repo, "master", "bad.txt", "x\n")
    loop.cycle()
    drive(lambda: state().get("main_red"))
    for _ in range(5):
        loop.cycle()
    st = state()
    assert all(j["status"] == "pending" for j in st["jobs"].values())
    assert sum("main is red" in a for a in alerts(st)) == 1


def test_a_state_from_an_older_loop_counts_as_green_once_a_job_moved():
    st = {"status": "running", "jobs": {"a": {"status": "working"}, "b": {"status": "pending"}}}
    assert loop._migrate(st)["base_green"] is True
    st = {"status": "running", "base_sha": "b" * 40, "jobs": {"a": {"status": "pending"}}}
    st = loop._migrate(st)
    assert st["base_green"] is False and st["refresh"]["status"] == "needs_check" and st["refresh"]["n"] == 0
    st = {"status": "waiting", "jobs": {}}
    assert "base_green" not in loop._migrate(st)


# ---- T6: main moves ----------------------------------------------------------------------------

def test_main_moving_moves_the_base_and_later_jobs_see_it(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    main = commit_on(repo, "master", "fix.txt", "from main\n")
    drive(lambda: any(r["result"] == "landed" and r["main"] == main for r in state()["refreshes"]))
    gitops.fetch(repo)
    assert gitops.is_ancestor(repo, main, gitops.remote_sha(repo, f"ark/{SLUG}"))
    assert not any("refreshed" in a for a in alerts(state()))  # Marco hears nothing for a landed refresh


def test_two_moves_between_looks_refresh_once_to_the_newest(feature, monkeypatch):
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    commit_on(repo, "master", "a.txt", "1\n")
    newest = commit_on(repo, "master", "b.txt", "2\n")
    drive(lambda: state()["refreshes"] and state()["refreshes"][-1]["result"] == "landed"
          and state()["refreshes"][-1]["n"] > 0)
    assert [r["main"] for r in state()["refreshes"] if r["n"] > 0] == [newest]


def test_a_red_main_keeps_the_base_and_alerts_once_per_commit(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    repo, _ = feature(manifest(integration_gate="test ! -e bad.txt"))
    drive(lambda: state()["base_green"])
    red = commit_on(repo, "master", "bad.txt", "x\n")
    drive(lambda: state().get("main_red"))
    for _ in range(5):
        loop.cycle()
    gitops.fetch(repo)  # jobs may merge meanwhile; the red main never gets in
    assert not gitops.is_ancestor(repo, red, gitops.remote_sha(repo, f"ark/{SLUG}"))
    assert state()["refreshes"][-1]["result"] == "main_red"
    assert sum("main is red" in a for a in alerts(state())) == 1
    git("rm", "-q", "bad.txt", cwd=repo)  # main turns green
    git("commit", "-q", "-m", "fix", cwd=repo)
    git("push", "-q", "origin", "master", cwd=repo)
    drive(lambda: not state().get("main_red") and state()["refreshes"][-1]["result"] == "landed")


def test_a_refresh_that_loses_the_race_with_a_job_merge_is_stale_and_tries_again(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    land = loop._land_refresh
    raced = []

    def racing(c, r):  # the base moves under the refresh, once, right before it lands
        if not raced:
            raced.append(commit_on(repo, f"ark/{SLUG}", "j.txt", "a job merged\n"))
        return land(c, r)
    monkeypatch.setattr(loop, "_land_refresh", racing)
    main = commit_on(repo, "master", "m.txt", "x\n")
    drive(lambda: any(r["result"] == "stale" for r in state()["refreshes"]))
    drive(lambda: state()["refreshes"][-1]["result"] == "landed")
    gitops.fetch(repo)
    tip = gitops.remote_sha(repo, f"ark/{SLUG}")
    assert gitops.is_ancestor(repo, main, tip) and gitops.is_ancestor(repo, raced[0], tip)


def test_a_clash_waits_and_starts_no_other_refresh(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    commit_on(repo, f"ark/{SLUG}", "c.txt", "base\n")
    commit_on(repo, "master", "c.txt", "main\n")
    drive(lambda: state()["refresh"] and state()["refresh"]["status"] == "clash")
    st = state()
    assert st["refresh"]["conflict"] == ["c.txt"]
    assert any("clashes with the run (c.txt)" in e for e in st["events"])
    commit_on(repo, "master", "d.txt", "more\n")
    for _ in range(3):
        loop.cycle()
    assert state()["refresh"]["n"] == st["refresh"]["n"]


def test_a_check_lost_without_a_result_starts_again_once_then_fails(feature):
    feature(manifest(integration_gate="true"))
    st = {"repo": "demo", "slug": SLUG, "status": "running", "events": [], "alerts": [], "jobs": {},
          "refresh": {"n": 3, "main": "m" * 40, "status": "checking"}, "refreshes": []}
    d = paths.run_dir("demo", SLUG)
    loop._reconcile_refresh(st, d)
    assert st["refresh"]["status"] == "needs_check"
    st["refresh"]["status"] = "checking"
    loop._reconcile_refresh(st, d)
    assert "refresh" not in st and st["refreshes"][-1]["result"] == "failed"
