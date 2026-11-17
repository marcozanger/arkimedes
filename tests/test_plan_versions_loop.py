"""The runner adopts a new plan version in any run state, and applies only what changed."""
import json

from ark import approve, forge, loop, paths, proc, run
from ark.plan import set_status
from control_helpers import drive
from helpers import job, manifest, plan_text

SLUG = "notes-edit"


def state():
    return run.load("demo", SLUG)


def _v(p, m):
    tasks = sorted({t for j in m["jobs"] for t in j["tasks"]})  # every task of the text belongs to a job
    p.write_text(plan_text(m, slug=SLUG, tasks=tasks))
    approve.approve_plan(p)


M1 = manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**"]), job("c", ["T3"], ["c/**"])])


def _start(runner, make_feature, monkeypatch, m=M1):
    monkeypatch.setenv("FAKE_SLEEP", "30")  # workers stay busy
    runner("demo")
    p = make_feature(plan_text(m, slug=SLUG), slug=SLUG, create_repo=False)
    approve.approve_plan(p)
    loop.cycle()
    assert state()["status"] == "running"
    return p


def test_editing_the_desk_never_holds_the_run(runner, make_feature, monkeypatch):
    p = _start(runner, make_feature, monkeypatch)
    set_status(p, "draft")
    p.write_text(p.read_text() + "\n<!-- thinking -->\n")
    loop.cycle()
    assert state()["status"] == "running"


def test_v2_restarts_only_the_changed_job(runner, make_feature, monkeypatch):
    p = _start(runner, make_feature, monkeypatch)
    before = json.loads(json.dumps(state()["jobs"]["a"]))
    _v(p, manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**", "b2/**"]),
                         job("d", ["T3"], ["d/**"])]))
    loop.cycle()
    st = state()
    assert st["jobs"]["a"]["status"] == before["status"] and st["jobs"]["a"]["attempts"] == before["attempts"]
    assert st["jobs"]["b"]["attempts"] <= 1  # started over from the new text
    assert "d" in st["jobs"] and "c" not in st["jobs"]
    assert "c" in st["retired_jobs"]
    assert not [n for n in proc.active(paths.run_dir("demo", SLUG)) if n.startswith("c.")]
    assert [v["version"] for v in st["versions"]] == [1, 2]
    assert st["versions"][1]["changes"] == {"a": "unchanged", "b": "changed", "c": "removed", "d": "added"}


def test_a_removed_job_with_a_pr_has_it_closed(runner, make_feature, monkeypatch):
    p = _start(runner, make_feature, monkeypatch)
    st = state()
    repo = paths.find_repo("demo")
    pr = forge.LocalForge().create(repo, base=f"ark/{SLUG}", head=f"ark/{SLUG}-c", title="c", body="")
    st["jobs"]["c"].update(status="needs_review", pr=pr)
    run.save(st)
    _v(p, manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**"])]))
    loop.cycle()
    assert forge.LocalForge().view(repo, pr["number"])["state"] == "CLOSED"


def test_a_changed_merged_job_becomes_a_follow_up(runner, make_feature, monkeypatch):
    p = _start(runner, make_feature, monkeypatch)
    st = state()
    st["jobs"]["a"]["status"] = "merged"
    run.save(st)
    _v(p, manifest(jobs=[job("a", ["T1"], ["a/**", "a2/**"]), job("b", ["T2"], ["b/**"]), job("c", ["T3"], ["c/**"])]))
    loop.cycle()
    a = state()["jobs"]["a"]
    assert a["status"] != "merged"
    assert a["followup"]["from_version"] == 1 and "a2/**" in a["followup"]["diff"]
    assert state()["retired_jobs"]["a@v1"]["status"] == "merged"


def test_a_removed_merged_job_stays_and_marco_is_told(runner, make_feature, monkeypatch):
    p = _start(runner, make_feature, monkeypatch)
    st = state()
    st["jobs"]["c"]["status"] = "merged"
    run.save(st)
    _v(p, manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**"])]))
    _, alerts, _ = loop.cycle()
    assert any("c" in a and "stays in main" in a for a in alerts)


def test_a_version_overlapping_another_run_is_refused_once(runner, make_feature, monkeypatch):
    p = _start(runner, make_feature, monkeypatch)
    other = make_feature(plan_text(manifest(jobs=[job("x", ["T1", "T2", "T3"], ["z/**"])]), slug="second"),
                         slug="second", create_repo=False)
    approve.approve_plan(other)
    loop.cycle()
    _v(p, manifest(jobs=[job("a", ["T1"], ["a/**", "z/**"]), job("b", ["T2"], ["b/**"]), job("c", ["T3"], ["c/**"])]))
    alerts = [a for _ in range(3) for a in loop.cycle()[1]]
    assert sum("cannot take v2" in a and "second" in a for a in alerts) == 1
    assert state()["versions"][-1]["version"] == 1


# -- a finished run starts again with a new version

def _settled():
    st = state()
    return st and st["status"] in ("done", "blocked")


def _done_run(runner, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    runner("demo")
    p = make_feature(plan_text(M1, slug=SLUG), slug=SLUG, create_repo=False)
    approve.approve_plan(p)
    drive(_settled)
    assert state()["status"] == "done"
    return p


def test_v2_of_a_done_run_runs_only_new_and_changed_work(runner, make_feature, monkeypatch):
    p = _done_run(runner, make_feature, monkeypatch)
    pr = state()["final_pr"]
    repo, f = paths.find_repo("demo"), forge.LocalForge()
    prs = f.list(repo)
    prs[pr["number"] - 1]["state"] = "MERGED"  # Marco merged the final PR
    f._save(repo, prs)
    _v(p, manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**", "b2/**"]),
                         job("c", ["T3"], ["c/**"]), job("d", ["T4"], ["d/**"])]))
    loop.cycle()
    st = state()
    assert st["status"] in ("waiting", "running")
    assert st["jobs"]["a"]["status"] == "merged" and st["jobs"]["c"]["status"] == "merged"
    assert st["jobs"]["b"]["followup"]["from_version"] == 1 and st["jobs"]["d"]["status"] != "merged"
    drive(_settled)
    st = state()
    assert st["status"] == "done" and st["final_pr"]["number"] != pr["number"]


def test_v2_of_a_done_run_with_an_open_final_pr_keeps_the_branch(runner, make_feature, monkeypatch):
    p = _done_run(runner, make_feature, monkeypatch)
    pr, base = state()["final_pr"], state()["base_sha"]
    _v(p, manifest(jobs=[*M1["jobs"], job("d", ["T4"], ["d/**"])]))
    loop.cycle()
    st = state()
    assert st["status"] == "running" and st["base_sha"] == base and st["final_pr"] == pr
    drive(_settled)
    assert state()["final_pr"]["number"] == pr["number"]


def test_v2_of_a_blocked_run_leaves_unchanged_blocked_jobs_blocked(runner, make_feature, monkeypatch):
    p = _start(runner, make_feature, monkeypatch)
    st = state()
    for j in st["jobs"].values():
        j["status"] = "blocked"
    st.update(status="blocked", closing="done")
    run.save(st)
    _v(p, manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**", "b2/**"]), job("c", ["T3"], ["c/**"])]))
    loop.cycle()
    st = state()
    assert st["jobs"]["a"]["status"] == "blocked" and st["jobs"]["b"]["status"] != "blocked"


def test_a_legacy_run_drops_a_job_its_first_version_removes(runner, make_feature, monkeypatch):
    from helpers import approve as legacy_approve
    monkeypatch.setenv("FAKE_SLEEP", "30")
    runner("demo")
    p = make_feature(plan_text(M1, slug=SLUG), slug=SLUG, create_repo=False)
    legacy_approve(p, slug=SLUG)
    loop.cycle()
    assert state()["status"] == "running"
    _v(p, manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**"])]))
    loop.cycle()
    loop.cycle()
    st = state()
    assert "c" not in st["jobs"] and "c" in st["retired_jobs"]
    assert st["versions"][-1]["changes"]["c"] == "removed"
