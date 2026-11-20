"""A clash between main and the run: a refresh worker merges main into the base, paid from the plan's merge budget."""
from types import SimpleNamespace

import pytest

from helpers import manifest
from test_loop import SLUG, drive, feature, state  # noqa: F401  (test_loop's own feature fixture)
from test_refresh_loop import commit_on, start_check  # noqa: F401  (the start check stays on)

from ark import brief, gitops, inbox, loop, paths, steps


@pytest.fixture(autouse=True)
def jobs_held(tmp_path, monkeypatch):
    """The jobs' workers stay alive through the test: no job moves the base under a refresh, the run stays open."""
    go = tmp_path / "go"
    monkeypatch.setenv("FAKE_HOLD", str(go))
    yield
    go.write_text("")


def proc_names(st) -> list[str]:
    """The process names of the run's steps (its steps.jsonl rows)."""
    return [f"{r['job']}.{r['kind']}" for r in steps.build(st) if r.get("job")]


def refresh_question(st) -> dict | None:
    return next((q for q in st.get("questions") or [] if q["kind"] == "refresh"), None)


def ctx(m, st=None):
    return SimpleNamespace(st=st or {}, plan=SimpleNamespace(manifest=m))


# ---- T7: budget and brief ------------------------------------------------------------------------

def test_the_refresh_budget_comes_from_the_plan_merge_entry(ark_home):
    m = manifest(merge={"tier": "T2", "budget": {"minutes": 30, "usd": 5}})
    assert loop._refresh_budget(ctx(m)) == ("T2", 5, 30)
    assert loop._refresh_budget(ctx(m, {"merge_usd": 1.5})) == ("T2", 3.5, 30)


def test_without_a_merge_entry_the_refresh_budget_is_the_workspace_cap(ark_home):
    assert paths.DEFAULTS["limits"]["refresh_usd"] == 3
    assert loop._refresh_budget(ctx(manifest(), {"merge_usd": 2})) == ("T1", 3, 40)


def test_the_refresh_brief_names_the_main_commit_the_files_and_the_gate():
    main = "a" * 40
    st = {"repo": "demo", "slug": SLUG}
    plan = SimpleNamespace(manifest=manifest(integration_gate="make check"))
    text = brief.refresh_brief(st, {"main": main, "conflict": ["a.txt", "b.txt"], "tail": "boom"}, plan, "master")
    assert f"git merge --no-ff {main}" in text and "`master`" in text
    assert "Conflicting files: a.txt, b.txt" in text and "`make check`" in text and "boom" in text
    assert text.rstrip().endswith("`STATUS: DONE` on its own line.")
    clean = brief.refresh_brief(st, {"main": main, "conflict": [], "tail": ""}, plan, "master")
    assert "the merge is clean but the integration gate is red" in clean


# ---- T8: the worker ------------------------------------------------------------------------------

def clash(repo, rel="a.txt", n=1) -> str:
    commit_on(repo, f"ark/{SLUG}", rel, f"run {n}\n")
    return commit_on(repo, "master", rel, f"main {n}\n")


def test_a_conflict_is_merged_by_the_refresh_worker_and_lands(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    monkeypatch.setenv("FAKE_REFRESH", "theirs")
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    main = clash(repo)
    drive(lambda: state()["refreshes"] and state()["refreshes"][-1]["result"] == "landed"
          and state()["refreshes"][-1]["n"] > 0)
    gitops.fetch(repo)
    head = gitops.remote_sha(repo, f"ark/{SLUG}")
    assert gitops.is_ancestor(repo, main, head)
    assert state().get("merge_usd", 0) > 0
    assert not loop._wt(state(), "_refresh-1").exists()


def test_a_worker_that_does_not_merge_main_asks_marco(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    monkeypatch.setenv("FAKE_REFRESH", "nothing")
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    main = clash(repo)
    drive(lambda: refresh_question(state()))
    q = refresh_question(state())
    assert main[:8] in q["text"] and "did not merge main" in q["text"]
    assert "`retry`" in q["text"] and "`skip`" in q["text"]


def test_a_red_gate_after_the_worker_asks_marco_with_its_tail(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    monkeypatch.setenv("FAKE_REFRESH", "theirs")
    gate = "if grep -q main a.txt 2>/dev/null; then echo gate-says-no; exit 1; fi"  # red only on main's a.txt
    repo, _ = feature(manifest(integration_gate=gate))
    drive(lambda: state()["base_green"])
    clash(repo)
    drive(lambda: refresh_question(state()))
    assert "gate-says-no" in refresh_question(state())["text"]


def test_the_merge_budget_is_spent_across_refreshes_then_asks(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    m = manifest(integration_gate="true")
    m["merge"] = {"tier": "T2", "budget": {"minutes": 30, "usd": 0.01}}
    monkeypatch.setenv("FAKE_REFRESH", "theirs")
    monkeypatch.setenv("FAKE_USD", "0.02")                  # every process says it cost $0.02
    repo, _ = feature(m)
    drive(lambda: state()["base_green"])
    clash(repo, "a.txt", 1)
    drive(lambda: state()["refreshes"] and state()["refreshes"][-1]["result"] == "landed"
          and state()["refreshes"][-1]["n"] > 0)
    assert state()["merge_usd"] >= 0.01
    clash(repo, "b.txt", 2)
    drive(lambda: refresh_question(state()))
    assert "budget" in refresh_question(state())["text"]
    workers = [n for n in proc_names(state()) if n.endswith(".worker") and n.startswith("_refresh-")]
    assert len(workers) == 1                                 # the second clash started none


def test_retry_starts_one_more_worker(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    monkeypatch.setenv("FAKE_REFRESH", "nothing")
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    main = clash(repo)
    drive(lambda: refresh_question(state()))
    n = state()["refresh"]["n"]
    monkeypatch.setenv("FAKE_REFRESH", "theirs")
    inbox.post("demo", SLUG, "answer", id=refresh_question(state())["id"], text="retry")
    drive(lambda: state()["refreshes"][-1]["result"] == "landed" and state()["refreshes"][-1]["n"] > 0)
    assert proc_names(state()).count(f"_refresh-{n}.worker") == 2  # a job merging meanwhile may make it stale
    assert not refresh_question(state())
    gitops.fetch(repo)
    assert gitops.is_ancestor(repo, main, gitops.remote_sha(repo, f"ark/{SLUG}"))


def test_an_answer_that_is_neither_asks_again(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    monkeypatch.setenv("FAKE_REFRESH", "nothing")
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    clash(repo)
    drive(lambda: refresh_question(state()))
    first = refresh_question(state())["id"]
    inbox.post("demo", SLUG, "answer", id=first, text="maybe")
    drive(lambda: refresh_question(state()) and refresh_question(state())["id"] != first)
    assert state()["refresh"]["status"] == "waiting_answer"


def test_skip_keeps_the_base_until_main_moves_again(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    monkeypatch.setenv("FAKE_REFRESH", "nothing")
    repo, _ = feature(manifest(integration_gate="true"))
    drive(lambda: state()["base_green"])
    main = clash(repo)
    drive(lambda: refresh_question(state()))
    inbox.post("demo", SLUG, "answer", id=refresh_question(state())["id"], text="skip")
    drive(lambda: state()["refreshes"][-1]["result"] == "skipped")
    n = state()["refreshes"][-1]["n"]
    for _ in range(3):
        loop.cycle()
    st = state()
    assert not st.get("refresh") and st["refreshes"][-1]["n"] == n
    gitops.fetch(repo)
    assert not gitops.is_ancestor(repo, main, gitops.remote_sha(repo, f"ark/{SLUG}"))
    monkeypatch.setenv("FAKE_REFRESH", "theirs")
    newer = commit_on(repo, "master", "z.txt", "later\n")
    drive(lambda: state()["refreshes"][-1]["result"] == "landed" and state()["refreshes"][-1]["n"] > n)
    gitops.fetch(repo)
    assert gitops.is_ancestor(repo, newer, gitops.remote_sha(repo, f"ark/{SLUG}"))


# ---- a gate red once on a clean merge is looked at again before a worker is paid ----------------------

def test_a_gate_red_once_on_a_clean_merge_is_flaky_and_starts_no_worker(feature, monkeypatch, tmp_path):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    monkeypatch.setattr(loop, "RECHECK_WAIT", 0)
    flag = tmp_path / "flaked"
    gate = f"if [ -e m.txt ] && [ ! -e {flag} ]; then touch {flag}; echo flaked-once; exit 1; fi"  # red once, with main in
    repo, _ = feature(manifest(integration_gate=gate))
    drive(lambda: state()["base_green"])
    main = commit_on(repo, "master", "m.txt", "from main\n")      # touches no file of the run: a clean merge
    drive(lambda: any(r["result"] == "landed" and r["main"] == main for r in state()["refreshes"]))
    st = state()
    assert not [n for n in proc_names(st) if n.startswith("_refresh-") and n.endswith(".worker")]
    assert st["flaky"][-1]["kind"] == "integrate" and st["flaky"][-1]["job"].startswith("_refresh-")
    assert any("checking once more before any worker" in e for e in st["events"])
    assert refresh_question(st) is None


def test_a_gate_red_twice_on_a_clean_merge_goes_to_the_worker_after_one_more_look(feature, monkeypatch):
    monkeypatch.setattr(loop, "REFRESH_EVERY", 0)
    monkeypatch.setattr(loop, "RECHECK_WAIT", 0)
    monkeypatch.setenv("FAKE_REFRESH", "nothing")
    gate = "if [ -e x.txt ] && [ -e y.txt ]; then echo both-present; exit 1; fi"  # red only on the merge of both
    repo, _ = feature(manifest(integration_gate=gate))
    drive(lambda: state()["base_green"])
    commit_on(repo, f"ark/{SLUG}", "x.txt", "run\n")
    commit_on(repo, "master", "y.txt", "main\n")
    drive(lambda: refresh_question(state()))
    st = state()
    assert sum("checking once more before any worker" in e for e in st["events"]) == 1   # one look, not a loop
    assert [n for n in proc_names(st) if n.startswith("_refresh-") and n.endswith(".worker")]
    assert not st.get("flaky")
