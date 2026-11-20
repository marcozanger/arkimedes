import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest
from helpers import approve, manifest, plan_text

from ark import forge, gitops, integrate, merge, paths, proc, run
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
    assert integrate.main([f"demo/{SLUG}", "shell", "0" * 40]) == 1
    line = json.loads(capsys.readouterr().out)
    assert line["ok"] is False and "corrupt" in line["reason"]
    assert state.read_text() == "{not json"


# --- helpers for the tests below ------------------------------------------------------------------

def set_pr(f, repo, number, **fields):
    prs = f.list(repo)
    prs[number - 1].update(fields)
    f._save(repo, prs)


def reason_of(ready_tuple, job="shell"):
    st, f, repo, *_ = ready_tuple
    return merge.check(st, job, f, repo)


def push_from_elsewhere(repo, tmp_path, branch, rel, text="elsewhere\n"):
    """Move a branch on origin without this repo's remote-tracking refs knowing."""
    clone = tmp_path / "elsewhere"
    subprocess.run(["git", "clone", "-q", "-b", branch, gitops.origin_url(repo), str(clone)], check=True)
    (clone / rel).write_text(text)
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-q", "-m", rel], check=True)
    subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", branch], check=True)


def base_tip(repo):
    return gitops.remote_sha(repo, f"ark/{SLUG}")


class Wrapped:
    """The real forge, with a scripted merge."""
    def __init__(self, real, on_merge):
        self.real, self.on_merge, self.merges = real, on_merge, 0

    def view(self, repo, number):
        return self.real.view(repo, number)

    def merge(self, repo, number, *, subject, head_sha):
        self.merges += 1
        self.on_merge(self.real, repo, number, subject, head_sha)


def merges_then(exc):
    def run_it(real, repo, number, subject, head_sha):
        real.merge(repo, number, subject=subject, head_sha=head_sha)
        raise exc
    return run_it


def fails_with(exc):
    def run_it(real, repo, number, subject, head_sha):
        raise exc
    return run_it


# --- I1: the outcome of a merge is what the forge shows afterwards --------------------------------

@pytest.mark.parametrize("exc", [forge.ForgeError("gh pr merge: HTTP 502"),
                                 forge.ForgeError("gh pr merge: timed out after 120s"),
                                 gitops.GitError("git push: boom"),
                                 RuntimeError("anything at all")])
def test_a_merge_that_happened_and_then_failed_reads_as_merged(ready, exc):
    st, f, repo, d, plan, base = ready()
    w = Wrapped(f, merges_then(exc))
    assert merge.merge(st, "shell", w, repo) is None
    assert f.view(repo, st["jobs"]["shell"]["pr"]["number"])["state"] == "MERGED"
    assert w.merges == 1


def test_a_rerun_after_the_merge_confirms_it_and_does_not_merge_again(ready):
    st, f, repo, d, plan, base = ready()
    merge.merge(st, "shell", f, repo)
    tip = base_tip(repo)
    w = Wrapped(f, fails_with(AssertionError("merged twice")))
    assert merge.merge(st, "shell", w, repo) is None
    assert w.merges == 0 and base_tip(repo) == tip


def test_a_rerun_confirms_even_when_the_head_branch_is_gone_or_the_checks_no_longer_hold(ready, tmp_path):
    st, f, repo, d, plan, base = ready()
    merge.merge(st, "shell", f, repo)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "--delete", f"ark/{SLUG}-shell"], check=True)
    (d / "shell.gate.json").unlink()
    assert merge.merge(st, "shell", Wrapped(f, fails_with(AssertionError("merged twice"))), repo) is None


def test_a_call_that_failed_with_no_merge_raises_with_its_reason(ready):
    st, f, repo, d, plan, base = ready()
    with pytest.raises(forge.ForgeError, match="HTTP 502"):
        merge.merge(st, "shell", Wrapped(f, fails_with(forge.ForgeError("HTTP 502"))), repo)
    assert f.view(repo, st["jobs"]["shell"]["pr"]["number"])["state"] == "OPEN"


def test_a_raw_failure_with_no_merge_comes_out_as_a_forge_error(ready):
    st, f, repo, d, plan, base = ready()
    with pytest.raises(forge.ForgeError, match="anything at all"):
        merge.merge(st, "shell", Wrapped(f, fails_with(RuntimeError("anything at all"))), repo)


def test_a_call_that_succeeded_with_no_merge_never_reads_as_merged(ready):
    """A base with a merge queue: gh queues the PR and exits 0."""
    st, f, repo, d, plan, base = ready()
    with pytest.raises(forge.ForgeError, match="OPEN"):
        merge.merge(st, "shell", Wrapped(f, lambda *a: None), repo)


def test_a_merge_at_another_head_never_reads_as_merged(ready):
    st, f, repo, d, plan, base = ready()
    number = st["jobs"]["shell"]["pr"]["number"]

    def someone_else(real, r, n, subject, head_sha):
        set_pr(real, r, n, state="MERGED", head_sha="1" * 40)
    with pytest.raises(forge.ForgeError, match="MERGED"):
        merge.merge(st, "shell", Wrapped(f, someone_else), repo)
    assert f.view(repo, number)["head_sha"] == "1" * 40
    with pytest.raises(merge.MergeRefused, match="MERGED"):
        merge.merge(st, "shell", f, repo)


@pytest.mark.parametrize("field,value", [("head_sha", "1" * 40), ("base", "ark/other"), ("head", "ark/other-shell")])
def test_a_merged_pr_counts_only_at_the_expected_head_and_branches(ready, field, value):
    st, f, repo, d, plan, base = ready()
    j = st["jobs"]["shell"]
    set_pr(f, repo, j["pr"]["number"], **{"state": "MERGED", "head_sha": j["head"], field: value})
    with pytest.raises(merge.MergeRefused):
        merge.merge(st, "shell", f, repo)


def test_an_unconfirmable_merge_raises_and_the_rerun_confirms(ready):
    st, f, repo, d, plan, base = ready()

    class ViewDies(Wrapped):
        dead = False

        def view(self, repo, number):
            if self.dead:
                raise forge.ForgeError("gh pr view: HTTP 503")
            return self.real.view(repo, number)

    w = ViewDies(f, merges_then(forge.ForgeError("gh pr merge: HTTP 502")))
    real_on_merge = w.on_merge

    def die_after(*a):
        w.dead = True
        real_on_merge(*a)
    w.on_merge = die_after
    with pytest.raises(forge.ForgeError, match="unknown"):
        merge.merge(st, "shell", w, repo)
    assert merge.merge(st, "shell", f, repo) is None


def test_an_exit_0_merge_whose_view_dies_is_not_read_as_merged(ready):
    st, f, repo, d, plan, base = ready()

    class ViewDies(Wrapped):
        dead = False

        def view(self, repo, number):
            if self.dead:
                raise RuntimeError("view died")
            return self.real.view(repo, number)

    w = ViewDies(f, lambda real, r, n, subject, head_sha: (real.merge(r, n, subject=subject, head_sha=head_sha),
                                                          setattr(w, "dead", True)))
    with pytest.raises(forge.ForgeError, match="unknown"):
        merge.merge(st, "shell", w, repo)


def test_workers_do_not_get_an_answer_from_merge_even_on_a_merged_pr(ready, monkeypatch):
    st, f, repo, d, plan, base = ready()
    merge.merge(st, "shell", f, repo)
    monkeypatch.setenv("ARK_ROLE", "worker")
    with pytest.raises(merge.MergeRefused, match="never merge"):
        merge.merge(st, "shell", f, repo)


FAKE_GH = """#!/bin/sh
case "$1 $2" in
  "pr view") cat "$FAKE_DIR/view.json" ;;
  "pr merge")
    echo "$@" >> "$FAKE_DIR/merges"
    case "$(cat "$FAKE_DIR/mode")" in
      merged_ok)   sed 's/OPEN/MERGED/' "$FAKE_DIR/view.json" > "$FAKE_DIR/v" && mv "$FAKE_DIR/v" "$FAKE_DIR/view.json" ;;
      merged_502)  sed 's/OPEN/MERGED/' "$FAKE_DIR/view.json" > "$FAKE_DIR/v" && mv "$FAKE_DIR/v" "$FAKE_DIR/view.json"
                   echo "HTTP 502" >&2; exit 1 ;;
      merged_hang) sed 's/OPEN/MERGED/' "$FAKE_DIR/view.json" > "$FAKE_DIR/v" && mv "$FAKE_DIR/v" "$FAKE_DIR/view.json"
                   touch "$FAKE_DIR/hanging"
                   sleep 30 ;;
      queued)      exit 0 ;;
      refused)     echo "not mergeable" >&2; exit 1 ;;
    esac ;;
esac
"""


@pytest.fixture
def fake_gh(ready, tmp_path, monkeypatch):
    def make(mode):
        st, _, repo, d, plan, base = ready()
        j = st["jobs"]["shell"]
        fd = tmp_path / "fakegh"
        (fd / "bin").mkdir(parents=True)
        (fd / "bin" / "gh").write_text(FAKE_GH)
        (fd / "bin" / "gh").chmod(0o755)
        (fd / "mode").write_text(mode)
        (fd / "view.json").write_text(json.dumps({
            "baseRefName": f"ark/{SLUG}", "headRefName": f"ark/{SLUG}-shell", "headRefOid": j["head"],
            "state": "OPEN", "url": "https://github.com/o/r/pull/1"}))
        monkeypatch.setenv("FAKE_DIR", str(fd))
        monkeypatch.setenv("PATH", f"{fd / 'bin'}:{os.environ['PATH']}")
        return st, repo, fd
    return make


@pytest.mark.parametrize("mode", ["merged_ok", "merged_502", "merged_hang"])
def test_gh_merged_reads_as_merged_however_the_call_ended_and_a_rerun_does_not_merge_again(fake_gh, mode, monkeypatch):
    # A load-proof clock (as in test_forge): a call meant to finish gets a limit no loaded host can exceed;
    # the hanging one is cut 1s after the fake says it hangs, not 1s after the process was spawned.
    monkeypatch.setattr(forge, "GH_TIMEOUT", 120)
    st, repo, fd = fake_gh(mode)
    real_popen = subprocess.Popen

    def popen(cmd, *a, **k):
        p = real_popen(cmd, *a, **k)
        if cmd[0] != "gh":
            return p
        real_wait = p.wait

        def wait(timeout=None):
            end = time.monotonic() + 120
            while not (fd / "hanging").exists() and p.poll() is None and time.monotonic() < end:
                time.sleep(0.02)
            return real_wait(timeout=1 if (fd / "hanging").exists() else timeout)
        p.wait = wait
        return p
    monkeypatch.setattr(forge.subprocess, "Popen", popen)
    assert merge.merge(st, "shell", forge.GhForge(), repo) is None
    assert merge.merge(st, "shell", forge.GhForge(), repo) is None
    assert len((fd / "merges").read_text().splitlines()) == 1


@pytest.mark.parametrize("mode", ["queued", "refused"])
def test_gh_not_merged_never_reads_as_merged(fake_gh, mode):
    st, repo, fd = fake_gh(mode)
    with pytest.raises(forge.ForgeError):
        merge.merge(st, "shell", forge.GhForge(), repo)


# --- I3: wrong-shape data is a reason or a FAIL, never a raw exception -----------------------------

@pytest.mark.parametrize("kind", ["gate", "verdict", "integrate"])
# The 1 MB param needs a short id: pytest puts the id in PYTEST_CURRENT_TEST, and on Linux an environment
# string over 128 KB makes every subprocess fail with E2BIG.
@pytest.mark.parametrize("text", ["[1]", "1", "null", '"ok"', "{not json", pytest.param("[" * 1000000, id="deeply-nested")])
def test_a_record_of_the_wrong_shape_is_no_record(ready, kind, text):
    r = ready()
    (r[3] / f"shell.{kind}.json").write_text(text)
    assert merge.read_record(r[3], "shell", kind) is None
    assert reason_of(r)


@pytest.mark.parametrize("text", ["[]", "1", "null", '{"sha256": null}', "{}", pytest.param("[" * 1000000, id="deeply-nested")])
def test_an_approval_of_the_wrong_shape_is_a_reason(ready, text):
    r = ready()
    paths.approval_file("demo", SLUG).write_text(text)
    assert reason_of(r) in ("no readable approval or plan", "the plan changed after its approval")


@pytest.mark.parametrize("damage", [
    lambda s: s.clear(),
    lambda s: s.update(jobs="oops"),
    lambda s: s.update(jobs=["shell"]),
    lambda s: s.update(jobs="shell"),
    lambda s: s["jobs"].update(shell="oops"),
    lambda s: s["jobs"]["shell"].update(pr="oops"),
    lambda s: s["jobs"]["shell"].update(pr={"number": "1"}),
    lambda s: s["jobs"]["shell"].update(pr={"number": True}),
    lambda s: s["jobs"]["shell"].update(pr={}),
    lambda s: s["jobs"]["shell"].update(head=7),
    lambda s: s["jobs"]["shell"].update(status=[], pr=None),
    lambda s: s["jobs"]["ui"].update(status="pending") or s["jobs"].update(shell=[1]),
    lambda s: s.update(plan=0),
    lambda s: s.update(slug=None),
    lambda s: s.update(repo=[]),
])
def test_a_state_of_the_wrong_shape_is_a_failure_and_merges_nothing(ready, damage, capsys):
    st, f, repo, d, plan, base = ready()
    tip = base_tip(repo)
    damage(st)
    assert isinstance(merge.check(st, "shell", f, repo), str)
    with pytest.raises(merge.MergeRefused):
        merge.merge(st, "shell", f, repo)
    state = run.state_file("demo", SLUG)
    text = json.dumps(st)
    state.write_text(text)
    assert merge.main([f"demo/{SLUG}", "shell"]) == 1
    assert capsys.readouterr().out.startswith("FAIL")
    assert state.read_text() == text and base_tip(repo) == tip


@pytest.mark.parametrize("text", ["{not json", "[1]", '{"a": 1}'])
def test_a_corrupt_local_forge_file_is_a_forge_error(ready, text, capsys):
    st, f, repo, d, plan, base = ready()
    f._file(repo).write_text(text)
    with pytest.raises(forge.ForgeError):
        merge.merge(st, "shell", f, repo)
    assert merge.main([f"demo/{SLUG}", "shell"]) == 1
    assert capsys.readouterr().out.startswith("FAIL")


def test_main_reports_any_exception_as_fail(ready, monkeypatch, capsys):
    ready()

    def boom():
        raise RuntimeError("no forge today")
    monkeypatch.setattr(forge, "get", boom)
    assert merge.main([f"demo/{SLUG}", "shell"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL") and "no forge today" in out


def test_integrate_main_on_a_state_of_the_wrong_shape_still_prints_its_json_line(ready, capsys):
    st, f, repo, d, plan, base = ready()
    run.state_file("demo", SLUG).write_text("{}")
    assert integrate.main([f"demo/{SLUG}", "shell", st["jobs"]["shell"]["head"]]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


# --- I5: one assertion per guard, each on its own --------------------------------------------------

def test_the_reviewer_role_cannot_merge(ready, monkeypatch):
    r = ready()
    monkeypatch.setenv("ARK_ROLE", "reviewer")
    assert "never merge" in reason_of(r)


@pytest.mark.parametrize("state", ["CLOSED", "MERGED", "DRAFT"])
def test_a_pr_that_is_not_open_is_refused_by_check(ready, state):
    r = ready()
    st, f, repo, *_ = r
    set_pr(f, repo, st["jobs"]["shell"]["pr"]["number"], state=state)
    assert reason_of(r) == f"the PR is {state}"


def test_the_approval_sha_is_checked_on_its_own(ready):
    r = ready()
    f = paths.approval_file("demo", SLUG)
    f.write_text(json.dumps({**json.loads(f.read_text()), "sha256": "0" * 64}))
    assert "plan changed" in reason_of(r)


def test_the_state_sha_is_checked_on_its_own(ready):
    r = ready()
    r[0]["plan_sha"] = "0" * 64
    assert "plan changed" in reason_of(r)


def test_a_job_with_no_state_entry_is_refused(ready):
    r = ready()
    del r[0]["jobs"]["shell"]
    assert "not in the approved manifest" in reason_of(r)


def test_a_state_job_that_the_manifest_does_not_have_is_refused(ready):
    r = ready()
    r[0]["jobs"]["ghost"] = dict(r[0]["jobs"]["shell"])
    assert "not in the approved manifest" in reason_of(r, "ghost")


@pytest.mark.parametrize("field,value", [("pr", None), ("head", None), ("head", 7), ("pr", {"number": True}),
                                         ("pr", {"number": "1"}), ("pr", "oops")])
def test_a_job_without_a_well_formed_pr_or_head_is_refused(ready, field, value):
    r = ready()
    r[0]["jobs"]["shell"][field] = value
    assert "no PR or head" in reason_of(r)


@pytest.mark.parametrize("kind,data,fragment", [
    ("gate", {"ok": False}, "gate"),
    ("gate", {"ok": "false"}, "gate"),
    ("gate", {"ok": True, "head": "0" * 40}, "gate"),
    ("verdict", {"verdict": "FIX"}, "CLEAN"),
    ("verdict", {"verdict": "CLEAN", "head": "0" * 40}, "CLEAN"),
    ("integrate", {"ok": False}, "integration"),
    ("integrate", {"ok": "false"}, "integration"),
    ("integrate", {"ok": True, "head": "0" * 40}, "integration"),
    ("integrate", {"ok": True, "base": "0" * 40}, "integration"),
])
def test_each_record_field_is_a_guard_of_its_own(ready, kind, data, fragment):
    r = ready()
    st, f, repo, d, plan, base = r
    good = {"gate": {"ok": True}, "verdict": {"verdict": "CLEAN"}, "integrate": {"ok": True, "base": base}}[kind]
    merge.record(d, "shell", kind, {**good, "head": st["jobs"]["shell"]["head"], **data})
    reason = reason_of(r)
    assert reason and fragment in reason, reason


@pytest.mark.parametrize("kind,fragment", [("gate", "gate"), ("verdict", "CLEAN"), ("integrate", "integration")])
def test_a_missing_record_is_refused(ready, kind, fragment):
    r = ready()
    (r[3] / f"shell.{kind}.json").unlink()
    assert fragment in reason_of(r)


def test_check_sees_the_base_on_origin_not_the_last_fetch(ready, tmp_path):
    r = ready()
    st, f, repo, d, plan, base = r
    push_from_elsewhere(repo, tmp_path, f"ark/{SLUG}", "other.txt")
    assert gitops.rev(repo, f"origin/ark/{SLUG}") == base  # not fetched yet: the record still looks current
    assert "integration" in reason_of(r)


def test_the_merge_subject_is_the_jobs_pr_title(ready):
    st, f, repo, d, plan, base = ready()
    merge.merge(st, "shell", f, repo)
    gitops.fetch(repo)
    assert gitops.git(repo, "log", "-1", "--format=%s", f"origin/ark/{SLUG}") == "feat: shell"


def test_main_success_merges_and_prints_ok(ready, capsys):
    st, f, repo, d, plan, base = ready()
    assert merge.main([f"demo/{SLUG}", "shell"]) == 0
    assert capsys.readouterr().out.strip() == "OK"
    assert f.view(repo, st["jobs"]["shell"]["pr"]["number"])["state"] == "MERGED"


def test_main_refusal_prints_fail_and_merges_nothing(ready, capsys):
    st, f, repo, d, plan, base = ready()
    (d / "shell.gate.json").unlink()
    assert merge.main([f"demo/{SLUG}", "shell"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL") and "gate" in out
    assert f.view(repo, st["jobs"]["shell"]["pr"]["number"])["state"] == "OPEN"
    assert base_tip(repo) == base


def test_main_without_a_run_or_a_repo_fails(ready, capsys):
    ready()
    assert merge.main(["demo/other-slug", "shell"]) == 1
    assert capsys.readouterr().out.strip() == "FAIL no run or no repo"
    assert merge.main(["bad-args"]) == 2


def test_integrate_refuses_a_conflict(ready, tmp_path):
    st, f, repo, d, plan, base = ready(manifest(integration_gate="touch gate-ran"))
    gitops.commit_file(repo, f"ark/{SLUG}", "src/shell/a.txt", "other\n", "conflict", tmp_path / "wt" / "c")
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is False and "does not merge cleanly" in out["reason"]


def test_integrate_runs_setup_first_in_the_scratch_worktree(ready):
    st, f, repo, d, plan, base = ready(manifest(setup=["touch setup-ran"], integration_gate="test -f setup-ran"))
    assert integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])["ok"] is True


def test_a_failing_setup_is_red_with_its_reason_and_the_gate_does_not_run(ready, tmp_path, monkeypatch):
    monkeypatch.setenv("MARK", str(tmp_path / "gate-ran"))
    st, f, repo, d, plan, base = ready(manifest(setup=["echo nope; exit 3"], integration_gate='touch "$MARK"'))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is False and "setup `echo nope; exit 3` failed (exit 3)" in out["reason"]
    assert "nope" in out["tail"] and not (tmp_path / "gate-ran").exists()


def test_the_gate_sees_the_jobs_head_on_the_current_base(ready, tmp_path):
    st, f, repo, d, plan, base = ready(manifest(integration_gate="test -f src/shell/a.txt && test -f other.txt"))
    gitops.commit_file(repo, f"ark/{SLUG}", "other.txt", "o\n", "moved", tmp_path / "wt" / "c")
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is True and out["base"] == base_tip(repo) != base


def test_integrate_main_prints_green_and_red_json(ready, capsys):
    st, f, repo, d, plan, base = ready()
    head = st["jobs"]["shell"]["head"]
    assert integrate.main([f"demo/{SLUG}", "shell", head]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "base": base, "head": head, "reason": "", "tail": ""}
    assert integrate.main([f"demo/{SLUG}", "shell", "0" * 40]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert integrate.main(["bad-args"]) == 2


# --- I2: the repo's hooks have no say in the runner's scratch work --------------------------------

def install_hook(repo, name, mark, hooks_dir=None):
    d = hooks_dir or (repo / ".git" / "hooks")
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(f'#!/bin/sh\ntouch "{mark}"\nexit 1\n')
    (d / name).chmod(0o755)


@pytest.mark.parametrize("hook", ["pre-commit", "prepare-commit-msg", "commit-msg", "post-commit",
                                  "post-checkout", "post-merge"])
def test_integrate_ignores_the_repos_hooks(ready, tmp_path, hook):
    st, f, repo, d, plan, base = ready()
    mark = tmp_path / "hook-ran"
    install_hook(repo, hook, mark)
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is True, out
    assert not mark.exists()


def test_integrate_ignores_a_configured_hooks_path(ready, tmp_path):
    st, f, repo, d, plan, base = ready()
    mark = tmp_path / "hook-ran"
    install_hook(repo, "pre-commit", mark, tmp_path / "husky")
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", str(tmp_path / "husky")], check=True)
    assert integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])["ok"] is True
    assert not mark.exists()


def test_integrate_does_not_sign_its_check_commit(ready):
    st, f, repo, d, plan, base = ready()
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "true"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "gpg.program", "false"], check=True)
    assert integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])["ok"] is True


# --- I4: the gate subprocess ------------------------------------------------------------------------

def is_gone(pid, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if not state or state.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def test_a_green_gate_with_bytes_that_are_not_utf8_is_green(ready):
    st, f, repo, d, plan, base = ready(manifest(integration_gate=r"printf '\377\376 ok\n'; exit 0"))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is True, out


def test_a_red_gate_with_bytes_that_are_not_utf8_keeps_its_tail(ready):
    st, f, repo, d, plan, base = ready(manifest(integration_gate=r"printf '\377 boom\n' >&2; exit 1"))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is False and "boom" in out["tail"] and "exit 1" in out["reason"]


def test_the_tail_is_the_end_of_both_streams_and_bounded(ready):
    gate = "echo first-out; echo then-err >&2; i=0; while [ $i -lt 5000 ]; do echo line$i; i=$((i+1)); done; echo last; exit 1"
    st, f, repo, d, plan, base = ready(manifest(integration_gate=gate))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["tail"].endswith("last\n") and len(out["tail"]) <= 3000 and "first-out" not in out["tail"]


def test_stderr_reaches_the_tail(ready):
    st, f, repo, d, plan, base = ready(manifest(integration_gate="echo oops >&2; exit 1"))
    assert "oops" in integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])["tail"]


def test_a_daemon_holding_the_pipes_does_not_delay_a_green_gate_and_does_not_outlive_integrate(ready, tmp_path, monkeypatch):
    monkeypatch.setenv("MARK", str(tmp_path / "pid"))
    st, f, repo, d, plan, base = ready(manifest(integration_gate='sleep 60 & echo $! > "$MARK"; exit 0'))
    t = time.monotonic()
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is True and time.monotonic() - t < 30
    assert is_gone(int((tmp_path / "pid").read_text()))


def test_a_service_setup_started_is_alive_for_the_gate_and_gone_afterwards(ready, tmp_path, monkeypatch):
    monkeypatch.setenv("MARK", str(tmp_path / "pid"))
    st, f, repo, d, plan, base = ready(manifest(setup=['sleep 60 >/dev/null 2>&1 & echo $! > "$MARK"'],
                                                integration_gate='kill -0 "$(cat "$MARK")"'))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is True, out
    assert is_gone(int((tmp_path / "pid").read_text()))


def test_a_gate_past_the_timeout_is_red_with_its_output_and_its_group_is_killed(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(integrate, "GATE_TIMEOUT", 1)
    monkeypatch.setenv("MARK", str(tmp_path / "pid"))
    st, f, repo, d, plan, base = ready(manifest(integration_gate='echo started; sleep 60 & echo $! > "$MARK"; wait'))
    t = time.monotonic()
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert time.monotonic() - t < 30
    assert out["ok"] is False and "integration gate timed out after 1s" in out["reason"]
    assert "started" in out["tail"]
    assert is_gone(int((tmp_path / "pid").read_text()))


def test_a_gate_whose_top_process_waits_for_all_its_children_is_not_held_by_the_reader(ready, monkeypatch):
    """The gate's one command replaces the shell, so it must not inherit the watching reader as a child."""
    monkeypatch.setattr(integrate, "GATE_TIMEOUT", 5)
    gate = f"{sys.executable} -c 'import os\ntry:\n    while True: os.wait()\nexcept ChildProcessError: pass'"
    st, f, repo, d, plan, base = ready(manifest(integration_gate=gate))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is True, out


def test_setup_and_gate_share_one_timeout(ready, monkeypatch):
    monkeypatch.setattr(integrate, "GATE_TIMEOUT", 2)
    st, f, repo, d, plan, base = ready(manifest(setup=["sleep 1.5"], integration_gate="sleep 1.5"))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is False and "timed out" in out["reason"]


def test_the_gate_has_no_stdin(ready, monkeypatch):
    """Even when the runner's own stdin is a pipe that never ends."""
    monkeypatch.setattr(integrate, "GATE_TIMEOUT", 10)
    st, f, repo, d, plan, base = ready(manifest(integration_gate="cat"))
    r, w = os.pipe()
    saved = os.dup(0)
    os.dup2(r, 0)
    try:
        assert integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])["ok"] is True
    finally:
        os.dup2(saved, 0)
        for fd in (saved, r, w):
            os.close(fd)


# --- Minors -----------------------------------------------------------------------------------------

def test_m1_the_merge_holds_the_run_lock_and_releases_it(ready):
    st, f, repo, d, plan, base = ready()
    seen = []

    def probe(real, r, n, subject, head_sha):
        with open(d / "merge.lock") as other, pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        seen.append("held")
        real.merge(r, n, subject=subject, head_sha=head_sha)
    merge.merge(st, "shell", Wrapped(f, probe), repo)
    assert seen == ["held"]
    with open(d / "merge.lock") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_m1_a_merge_that_raises_releases_the_lock(ready):
    st, f, repo, d, plan, base = ready()
    with pytest.raises(forge.ForgeError):
        merge.merge(st, "shell", Wrapped(f, fails_with(forge.ForgeError("x"))), repo)
    with open(d / "merge.lock") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("status", ["done", "held", "waiting"])
def test_m2_a_run_that_is_not_active_is_refused(ready, status):
    r = ready()
    r[0]["status"] = status
    assert reason_of(r) == f"the run is {status}"


def test_m2_a_run_marked_blocked_keeps_merging_the_jobs_that_are_not(ready):
    """§5: a blocked job holds its dependents, the rest continues."""
    r = ready()
    r[0]["status"] = "blocked"
    assert reason_of(r) is None


@pytest.mark.parametrize("status", ["blocked", "merged"])
def test_m2_a_job_that_is_finished_is_refused(ready, status):
    r = ready()
    r[0]["jobs"]["shell"]["status"] = status
    assert reason_of(r) == f"the job is {status}"


def test_m2_a_job_waits_for_the_jobs_it_comes_after(ready):
    r = ready()
    assert reason_of(r, "ui") == "the job waits for shell"
    r[0]["jobs"]["shell"]["status"] = "merged"
    assert "no PR or head" in reason_of(r, "ui")


def test_m3_a_head_that_changes_nothing_is_refused(ready, tmp_path):
    r = ready()
    st, f, repo, d, plan, base = r
    wt = tmp_path / "w" / f"ark-{SLUG}-shell"
    subprocess.run(["git", "-C", str(wt), "rm", "-q", "src/shell/a.txt"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-qm", "revert"], check=True)
    gitops.push_branch(wt, f"ark/{SLUG}-shell")
    st["jobs"]["shell"]["head"] = gitops.rev(wt, "HEAD")
    assert "changes nothing" in reason_of(r)


def test_m4_integrate_never_runs_a_gate_that_was_not_approved(ready, tmp_path, monkeypatch):
    monkeypatch.setenv("MARK", str(tmp_path / "ran"))
    st, f, repo, d, plan, base = ready()
    edited = plan.read_text().replace("integration_gate: 'true'", """integration_gate: 'touch "$MARK"'""")
    assert edited != plan.read_text()
    plan.write_text(edited)
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] is False and "plan changed" in out["reason"]
    assert not (tmp_path / "ran").exists()


READ_ONLY = "mkdir ro && touch ro/f && chmod -R a-w ro"


def test_m5_a_green_gate_that_leaves_a_read_only_dir_stays_green_and_cleans_up(ready):
    st, f, repo, d, plan, base = ready(manifest(integration_gate=READ_ONLY))
    head = st["jobs"]["shell"]["head"]
    assert integrate.integrate("demo", SLUG, "shell", head)["ok"] is True
    assert not list((paths.worktrees_dir() / "demo" / SLUG).glob("_integrate-*"))


def test_m5_a_red_gate_that_leaves_a_read_only_dir_keeps_its_reason(ready):
    st, f, repo, d, plan, base = ready(manifest(integration_gate=READ_ONLY + "; echo boom; exit 1"))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert "integration gate failed" in out["reason"] and "boom" in out["tail"]


def test_m5_a_cleanup_failure_does_not_replace_the_outcome(ready, monkeypatch):
    st, f, repo, d, plan, base = ready()

    def boom(*a):
        raise gitops.GitError("cannot remove")
    real = gitops.squash_keep

    def keep(*a):
        sha = real(*a)
        monkeypatch.setattr(gitops, "remove_worktree", boom)
        return sha
    monkeypatch.setattr(gitops, "squash_keep", keep)
    assert integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])["ok"] is True


# --- Stopping ark-integrate stops its gate ----------------------------------------------------------

CHECK = "shell.integrate"
SETUP = 'sleep 30 >/dev/null 2>&1 & echo $! > "$MARK.setup"'
# Started once, then green at once for the check that follows. It ignores what a polite stop sends.
GATE = ('[ -e "$MARK" ] && exit 0; trap "" TERM INT HUP; sleep 30 & echo $! > "$MARK.daemon"; '
        'echo $PPID > "$MARK.parent"; echo $$ > "$MARK"; sleep 30')


def scratch_left():
    return list((paths.worktrees_dir() / "demo" / SLUG).glob("_integrate-*"))


def step_env():
    return {**os.environ, "PYTHONPATH": str(paths.code_dir() / "lib")}


class Check:
    """A real ark-integrate with its real gate, waiting in that gate. Every pid is a child of this test."""
    def __init__(self, ready, mark, gate):
        self.mark = mark
        self.st, self.f, self.repo, self.d, *_ = ready(manifest(setup=[SETUP], integration_gate=gate))
        self.head = self.st["jobs"]["shell"]["head"]
        self.argv = [sys.executable, "-m", "ark.integrate", f"demo/{SLUG}", "shell", self.head]
        self.popen = self.pids = None

    def wait_for_gate(self):
        end = time.monotonic() + 20
        while not self.mark.exists():
            assert time.monotonic() < end, f"the gate never started: {self.output()}"
            time.sleep(0.05)
        self.parent = int(self.mark.with_name(self.mark.name + ".parent").read_text())
        self.gate = int(self.mark.read_text())
        self.pids = [self.gate] + [int(self.mark.with_name(f"{self.mark.name}.{s}").read_text())
                                   for s in ("setup", "daemon")]

    def output(self):
        log = self.d / f"{CHECK}.log"
        return log.read_text(errors="replace") if log.exists() else "no log"

    def under_proc(self):
        self.info = proc.start(self.d, CHECK, self.argv, cwd=self.repo, env=step_env(), minutes=5)
        self.wait_for_gate()
        return self

    def in_a_terminal(self):
        self.popen = subprocess.Popen(self.argv, cwd=self.repo, env=step_env(), start_new_session=True,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.wait_for_gate()
        return self

    def all_gone(self):
        return [pid for pid in self.pids if not is_gone(pid)] == []

    def receipt(self):
        return json.loads((self.d / f"{CHECK}.receipt.json").read_text())

    def clean_up(self):
        for pid in self.pids or ():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        child = proc._children.pop(str(self.d / CHECK), None)
        for p in (child, self.popen):
            if p is not None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.wait()


@pytest.fixture
def check(ready, tmp_path, monkeypatch):
    mark = tmp_path / "gate"
    monkeypatch.setenv("MARK", str(mark))
    made = []

    def make(gate=GATE):
        made.append(Check(ready, mark, gate))
        return made[-1]
    yield make
    for c in made:
        c.clean_up()


def past_its_deadline(c):
    path = c.d / f"{CHECK}.proc"
    proc._atomic(path, {**json.loads(path.read_text()), "deadline": 0})
    assert proc.reap(c.d) == [CHECK]


STOPS = {
    "proc_stop": lambda c: proc.stop(c.d, CHECK, "stopped by Marco"),
    "deadline": past_its_deadline,
    "sigterm_to_group": lambda c: os.killpg(c.info["pid"], signal.SIGTERM),
    "sigint_to_group": lambda c: os.killpg(c.info["pid"], signal.SIGINT),
    "sighup_to_group": lambda c: os.killpg(c.info["pid"], signal.SIGHUP),
    "sigterm_to_integrate_only": lambda c: os.kill(c.parent, signal.SIGTERM),
}


@pytest.mark.parametrize("how", sorted(STOPS))
def test_stopping_the_check_kills_its_gate_and_what_setup_started_and_removes_the_worktree(check, how):
    c = check().under_proc()
    assert c.parent != c.info["pid"]  # the gate's parent is ark-integrate, under the wrapper
    STOPS[how](c)
    assert c.all_gone()
    assert is_gone(c.parent)
    assert proc._group_ended(c.info["pid"], proc.KILL_WAIT)
    assert not scratch_left()
    if how in ("proc_stop", "deadline"):
        assert c.receipt().get("survivors") is None
        assert c.receipt()["reason"] == ("stopped by Marco" if how == "proc_stop" else "timeout: over its time limit")


@pytest.mark.parametrize("victim", ["integrate", "group"])
def test_a_sigkill_gives_no_chance_to_clean_up_and_still_kills_the_gate(check, victim):
    c = check().under_proc()
    if victim == "integrate":
        os.kill(c.parent, signal.SIGKILL)
    else:
        os.killpg(c.info["pid"], signal.SIGKILL)
    assert c.all_gone()
    out = integrate.integrate("demo", SLUG, "shell", c.head)  # the leftover worktree does not wedge the next check
    assert out["ok"] is True, out
    assert not scratch_left()


def test_repeated_signals_do_not_interrupt_the_cleanup(check):
    """A stop delivers SIGTERM twice: from proc.stop, then from the watcher once the wrapper is gone.
    The read-only directory makes git's removal fail, so the cleanup must get as far as its own rmtree."""
    c = check(f"{READ_ONLY}; {GATE}").in_a_terminal()
    for _ in range(25):
        os.kill(c.popen.pid, signal.SIGTERM)
        time.sleep(0.02)
    assert c.popen.wait(timeout=20) == 128 + signal.SIGTERM
    assert c.all_gone()
    assert not scratch_left()


def test_a_step_interrupted_kills_its_group_before_returning(tmp_path):
    """Before integrate's `finally` removes the worktree. The pipe stays open: the kill is the step's own."""
    mark = tmp_path / "pid"
    r, w = os.pipe()

    def interrupt(signum, frame):
        raise SystemExit(1)
    before = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        with pytest.raises(SystemExit):
            integrate._step(f'echo $$ > "{mark}"; sleep 30', tmp_path, 20, r)
        assert proc._group_members(int(mark.read_text())) == set()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, before)
        os.close(r)
        os.close(w)


def test_a_check_stopped_during_a_setup_step_kills_that_step(ready, tmp_path, monkeypatch):
    mark = tmp_path / "gate"
    monkeypatch.setenv("MARK", str(mark))
    st, f, repo, d, *_ = ready(manifest(setup=['echo $$ > "$MARK"; sleep 30'], integration_gate="true"))
    argv = [sys.executable, "-m", "ark.integrate", f"demo/{SLUG}", "shell", st["jobs"]["shell"]["head"]]
    proc.start(d, CHECK, argv, cwd=repo, env=step_env(), minutes=5)
    try:
        end = time.monotonic() + 20
        while not mark.exists() or not mark.read_text().strip():
            assert time.monotonic() < end
            time.sleep(0.05)
        proc.stop(d, CHECK, "stopped by Marco")
        assert is_gone(int(mark.read_text()))
        assert not scratch_left()
    finally:
        if mark.exists() and mark.read_text().strip():
            try:
                os.kill(int(mark.read_text()), signal.SIGKILL)
            except OSError:
                pass
        proc._children.pop(str(d / CHECK)).wait()


def test_a_finished_check_leaves_nothing_in_the_gates_group(ready, tmp_path, monkeypatch):
    monkeypatch.setenv("MARK", str(tmp_path / "pid"))
    st, f, repo, d, *_ = ready(manifest(integration_gate='echo $$ > "$MARK"'))
    assert integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])["ok"] is True
    assert proc._group_ended(int((tmp_path / "pid").read_text()), 5)


def test_a_gate_that_waits_for_its_own_jobs_is_not_held_up_by_what_watches_it(ready, monkeypatch):
    monkeypatch.setattr(integrate, "GATE_TIMEOUT", 20)
    st, f, repo, d, *_ = ready(manifest(integration_gate="sleep 0.1 & wait"))
    t = time.monotonic()
    assert integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])["ok"] is True
    assert time.monotonic() - t < 15


def test_main_gives_back_the_signal_handlers(ready):
    st, f, repo, d, *_ = ready()
    before = [signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)]
    assert integrate.main([f"demo/{SLUG}", "shell", st["jobs"]["shell"]["head"]]) == 0
    assert [signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)] == before


# --- The merge lock ---------------------------------------------------------------------------------

def test_a_stuck_holder_of_the_lock_is_a_retryable_error_not_a_hang(ready, monkeypatch):
    monkeypatch.setattr(merge, "LOCK_WAIT", 0.2)
    st, f, repo, d, *_ = ready()
    wrapped = Wrapped(f, fails_with(AssertionError("must not merge without the lock")))
    with open(d / "merge.lock", "a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        t = time.monotonic()
        with pytest.raises(forge.ForgeError, match="merge lock"):
            merge.merge(st, "shell", wrapped, repo)
        assert time.monotonic() - t < 5
    assert wrapped.merges == 0
    merge.merge(st, "shell", f, repo)  # released: the retry goes through


def test_a_holder_that_finishes_within_the_wait_does_not_fail_the_merge(ready, monkeypatch):
    monkeypatch.setattr(merge, "LOCK_WAIT", 20)
    st, f, repo, d, *_ = ready()
    holder = open(d / "merge.lock", "a")
    fcntl.flock(holder, fcntl.LOCK_EX)
    timer = threading.Timer(0.3, holder.close)
    timer.start()
    try:
        merge.merge(st, "shell", f, repo)
    finally:
        timer.join()
    assert f.view(repo, st["jobs"]["shell"]["pr"]["number"])["state"] == "MERGED"


def test_m1_the_lock_also_covers_the_checks_before_the_merge(ready):
    st, f, repo, d, *_ = ready()
    seen = []

    class Probing(Wrapped):
        def view(self, r, number):
            with open(d / "merge.lock") as other, pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append("held")
            return super().view(r, number)
    merge.merge(st, "shell", Probing(f, lambda real, r, n, subject, head_sha:
                                     real.merge(r, n, subject=subject, head_sha=head_sha)), repo)
    assert seen


def test_m5_a_gate_that_leaves_the_top_of_the_worktree_read_only_stays_green_and_cleans_up(ready):
    st, f, repo, d, *_ = ready(manifest(integration_gate="chmod a-w ."))
    head = st["jobs"]["shell"]["head"]
    assert integrate.integrate("demo", SLUG, "shell", head)["ok"] is True
    assert not scratch_left()
    assert integrate.integrate("demo", SLUG, "shell", head)["ok"] is True  # no wedge


def test_a_record_does_not_write_through_a_planted_tmp_link(tmp_path):
    d, victim = tmp_path / "run", tmp_path / "victim"
    d.mkdir()
    victim.write_text("mine")
    (d / "shell.gate.json.tmp").symlink_to(victim)
    merge.record(d, "shell", "gate", {"ok": True, "head": "abc"})
    assert victim.read_text() == "mine"
    assert json.loads((d / "shell.gate.json").read_text())["head"] == "abc"


def test_a_record_does_not_create_the_target_of_a_dangling_tmp_link(tmp_path):
    d, victim = tmp_path / "run", tmp_path / "victim"
    d.mkdir()
    (d / "shell.gate.json.tmp").symlink_to(victim)
    merge.record(d, "shell", "gate", {"ok": True, "head": "abc"})
    assert not victim.exists()


# ---- ark-integrate: a name from the command line deletes nothing ---------------------------------

@pytest.mark.parametrize("job", ["nojob", "../nope", "../../shell", "../../../../victim", "", "shell/.", "."])
def test_integrate_refuses_a_job_that_is_not_in_the_run_and_touches_nothing(ready, capsys, monkeypatch, job):
    st, _, repo, _, _, _ = ready()
    root = paths.worktrees_dir()
    live, victim = root / "demo" / SLUG / "shell", root / "victim"
    for d in (live, victim, root / "demo" / SLUG / "_integrate-.."):            # what an earlier call left
        d.mkdir(parents=True)
    (live / "unsaved").write_text("work")
    (victim / "file").write_text("work")
    monkeypatch.setattr(gitops, "fetch", lambda repo: pytest.fail("reached git"))
    assert integrate.main([f"demo/{SLUG}", job, st["jobs"]["shell"]["head"]]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "not in the approved manifest" in out["reason"]
    assert (live / "unsaved").read_text() == "work" and (victim / "file").read_text() == "work"
    assert not list(root.glob(f"demo/{SLUG}/_integrate-[!.]*"))


@pytest.mark.parametrize("where", ["state", "manifest"])
def test_integrate_wants_the_job_in_both_the_state_and_the_manifest(ready, monkeypatch, where):
    st, *_ = ready()
    head = st["jobs"]["shell"]["head"]
    if where == "state":
        st["jobs"]["ghost"] = dict(st["jobs"]["shell"])
        run.save(st)
        job = "ghost"
    else:
        del st["jobs"]["shell"]
        run.save(st)
        job = "shell"
    monkeypatch.setattr(gitops, "fetch", lambda repo: pytest.fail("reached git"))
    out = integrate.integrate("demo", SLUG, job, head)
    assert out["ok"] is False and "not in the approved manifest" in out["reason"]


@pytest.mark.parametrize("spec", [f"demo/./{SLUG}", f"demo//{SLUG}", f"./demo/{SLUG}"])
def test_integrate_refuses_another_spelling_of_the_run(ready, capsys, monkeypatch, spec):
    st, *_ = ready()
    monkeypatch.setattr(gitops, "fetch", lambda repo: pytest.fail("reached git"))
    assert integrate.main([spec, "shell", st["jobs"]["shell"]["head"]]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


# ---- a FIFO in the run folder ---------------------------------------------------------------------

@pytest.fixture
def must_not_hang():
    def alarm(*_):
        raise AssertionError("hung on a FIFO")
    before = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, 30)
    yield
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, before)


def test_a_fifo_as_a_record_is_no_record(ready, must_not_hang):
    d = ready()[3]
    (d / "shell.gate.json").unlink()
    os.mkfifo(d / "shell.gate.json")
    assert merge.read_record(d, "shell", "gate") is None


@pytest.mark.parametrize("how", ["fifo", "link"])
def test_a_merge_lock_that_is_not_a_regular_file_refuses_the_merge(ready, must_not_hang, tmp_path, how):
    st, f, repo, d, *_ = ready()
    victim = tmp_path / "victim"
    if how == "fifo":
        os.mkfifo(d / "merge.lock")
    else:
        (d / "merge.lock").symlink_to(victim)
    with pytest.raises(merge.MergeRefused, match="merge lock"):
        merge.merge(st, "shell", f, repo)
    assert not victim.exists()
