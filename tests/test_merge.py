import json
import subprocess

import pytest
from helpers import approve, manifest, plan_text

from ark import forge, gitops, integrate, merge, paths, run
from ark.plan import load_plan

SLUG = "notes-edit"


def commit_on(repo, tmp_path, branch, start, rel, text="x\n"):
    wt = tmp_path / "w" / branch.replace("/", "-")
    gitops.add_worktree(repo, wt, branch, start)
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text(text)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", rel], check=True)
    gitops.push_branch(wt, branch)
    return gitops.rev(wt, "HEAD")


@pytest.fixture
def ready(runner, make_feature, tmp_path):
    """A run whose job `shell` has a PR that passes every check."""
    def make(m=None):
        repo = runner()
        plan = make_feature(plan_text(m), create_repo=False)
        st = run.new_state(json.loads(approve(plan).read_text()))
        st.update(status="running", default_branch="master")
        for mj in load_plan(plan).manifest["jobs"]:
            st["jobs"][mj["id"]] = run.new_job(mj)
        base = gitops.rev(repo, "HEAD")
        gitops.push_ref(repo, base, f"ark/{SLUG}")
        head = commit_on(repo, tmp_path, f"ark/{SLUG}-shell", base, "src/shell/a.txt")
        f = forge.LocalForge()
        pr = f.create(repo, base=f"ark/{SLUG}", head=f"ark/{SLUG}-shell", title="feat: shell", body="")
        st["jobs"]["shell"].update(head=head, pr=pr)
        run.save(st)
        d = paths.run_dir("demo", SLUG)
        merge.record(d, "shell", "gate", {"ok": True, "head": head})
        merge.record(d, "shell", "verdict", {"verdict": "CLEAN", "head": head, "findings": []})
        merge.record(d, "shell", "integrate", {"ok": True, "base": base, "head": head})
        return st, f, repo, d, plan, base
    return make


def test_all_checks_hold_and_merge(ready):
    st, f, repo, d, plan, base = ready()
    assert merge.check(st, "shell", f, repo) is None
    merge.merge(st, "shell", f, repo)
    gitops.fetch(repo)
    assert gitops.changed_files(repo, base, f"origin/ark/{SLUG}") == ["src/shell/a.txt"]


def test_refusals(ready, tmp_path, monkeypatch):
    st, f, repo, d, plan, base = ready()
    j = st["jobs"]["shell"]

    def refused(fragment):
        reason = merge.check(st, "shell", f, repo)
        assert reason and fragment in reason, reason
        with pytest.raises(merge.MergeRefused):
            merge.merge(st, "shell", f, repo)

    monkeypatch.setenv("ARK_ROLE", "worker")
    refused("never merge")
    monkeypatch.delenv("ARK_ROLE")

    d_gate = merge.read_record(d, "shell", "gate")
    merge.record(d, "shell", "gate", {"ok": True, "head": "0" * 40})
    refused("gate")
    merge.record(d, "shell", "gate", d_gate)

    merge.record(d, "shell", "verdict", {"verdict": "FIX", "head": j["head"], "findings": ["x"]})
    refused("CLEAN")
    (d / "shell.verdict.json").unlink()
    refused("CLEAN")
    merge.record(d, "shell", "verdict", {"verdict": "CLEAN", "head": j["head"], "findings": []})

    pr = j["pr"]
    class OtherBase:  # the forge refuses a second PR on the same head with another base
        view = staticmethod(lambda r, n, real=f: {**real.view(r, n), "base": "master"})
        merge = f.merge

    real_forge, f = f, OtherBase()
    refused("base")
    f = real_forge
    j["pr"] = f.create(repo, base=f"ark/{SLUG}", head=f"ark/{SLUG}-ui", title="t", body="")
    refused("head branch")
    j["pr"] = pr

    assert merge.check(st, "ghost", f, repo) == "job ghost is not in the approved manifest"

    gitops.commit_file(repo, f"ark/{SLUG}", "other.txt", "o\n", "moved", tmp_path / "scratch")
    refused("integration")
    gitops.fetch(repo)
    merge.record(d, "shell", "integrate", {"ok": True, "base": gitops.rev(repo, f"origin/ark/{SLUG}"),
                                           "head": j["head"]})
    assert merge.check(st, "shell", f, repo) is None

    plan.write_text(plan.read_text() + "\nedited\n")
    refused("plan changed")


def test_refuses_files_outside_the_area_and_a_moved_head(ready, tmp_path):
    st, f, repo, d, plan, base = ready()
    j = st["jobs"]["shell"]
    wt = tmp_path / "w" / f"ark-{SLUG}-shell"
    (wt / "README.md").write_text("changed\n")
    subprocess.run(["git", "-C", str(wt), "commit", "-qam", "outside"], check=True)
    gitops.push_branch(wt, f"ark/{SLUG}-shell")
    assert "moved" in merge.check(st, "shell", f, repo)
    j["head"] = gitops.rev(wt, "HEAD")
    assert "outside the job's area: README.md" in merge.check(st, "shell", f, repo)


def test_integrate_green_and_red(ready):
    st, f, repo, d, plan, base = ready()
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] and out["base"] == base and out["head"] == st["jobs"]["shell"]["head"]
    assert not list((paths.worktrees_dir() / "demo" / SLUG).glob("_integrate-*"))


def test_integrate_red(ready):
    st, f, repo, d, plan, base = ready(manifest(integration_gate="echo boom; exit 1"))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert not out["ok"] and "integration gate failed" in out["reason"] and "boom" in out["tail"]


def test_corrupt_state_is_a_failure_and_is_never_replaced(ready, capsys):
    st, f, repo, d, plan, base = ready()
    state = run.state_file("demo", SLUG)
    state.write_text("{not json")
    assert merge.main([f"demo/{SLUG}", "shell"]) == 1
    shown = capsys.readouterr().out
    assert shown.startswith("FAIL") and "corrupt" in shown
    out = integrate.main([f"demo/{SLUG}", "shell", "0" * 40])
    assert out == 1 and '"ok": false' in capsys.readouterr().out
    assert state.read_text() == "{not json"
