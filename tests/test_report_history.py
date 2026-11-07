import json
import subprocess

from report_helpers import job, mjobs, receipt, result_line, run_state


def summarize(st):
    from ark import steps, summary
    return summary.summarize(st, steps.build(st), mjobs())


def lines(name):
    from ark import history
    return [json.loads(x) for x in (history.history_dir() / name).read_text().splitlines()]


def test_one_row_per_job_and_one_per_plan(ark_home):
    from ark import history
    receipt("shell.worker", 0, 10, usd=1.0, log=result_line(usd=1.0))
    st = run_state(jobs={"shell": job("merged", tier_path=["T1", "T2"]),
                         "ui": job("blocked", reason="job gate red (exit 1): boom")})
    history.record(st, summarize(st), mjobs(), None)
    shell, ui = lines("jobs.jsonl")
    assert (shell["repo"], shell["plan"], shell["job"]) == ("demo", "notes-edit", "shell")
    assert (shell["tier_start"], shell["tier_end"], shell["outcome"], shell["usd"]) == ("T1", "T2", "merged", 1.0)
    assert shell["tasks"] == ["T1", "T2"] and shell["area"] == 2 and shell["budget_usd"] == 3
    assert (shell["files"], shell["size"]) == (None, "?")                      # no repo given
    assert ui["failure"] == "job gate red" and shell["failure"] is None
    [plan] = lines("plans.jsonl")
    assert (plan["plan"], plan["merged"], plan["blocked"], plan["usd"]) == ("notes-edit", 1, 1, 1.0)


def test_recording_a_run_again_replaces_its_rows(ark_home):
    from ark import history
    st = run_state()
    other = run_state(slug="other")
    history.record(st, summarize(st), mjobs(), None)
    history.record(other, summarize(other), mjobs(), None)
    history.record(st, summarize(st), mjobs(), None)
    assert [(r["plan"], r["job"]) for r in lines("jobs.jsonl")] == \
        [("other", "shell"), ("other", "ui"), ("notes-edit", "shell"), ("notes-edit", "ui")]
    assert [r["plan"] for r in lines("plans.jsonl")] == ["other", "notes-edit"]


def test_size_comes_from_the_job_diff(ark_home, make_repo):
    from ark import history
    repo = make_repo("demo")
    git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    (repo / "a.txt").write_text("a\n")
    git("add", "-A"); git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    for i in range(5):
        (repo / f"f{i}.txt").write_text("x\ny\n")
    git("add", "-A"); git("commit", "-q", "-m", "work")
    head = git("rev-parse", "HEAD")
    st = run_state(jobs={"shell": job(base=base, head=head), "ui": job(base=base, head="0" * 40)})
    history.record(st, summarize(st), mjobs(), repo)
    shell, ui = lines("jobs.jsonl")
    assert (shell["files"], shell["lines"], shell["size"]) == (5, 10, "M")
    assert (ui["files"], ui["size"]) == (None, "?")                             # commits gone: unknown


def test_a_broken_history_line_is_dropped_not_fatal(ark_home):
    from ark import history
    history.history_dir().mkdir(parents=True)
    (history.history_dir() / "jobs.jsonl").write_text("{broken\n")
    st = run_state()
    history.record(st, summarize(st), mjobs(), None)
    assert len(lines("jobs.jsonl")) == 2
