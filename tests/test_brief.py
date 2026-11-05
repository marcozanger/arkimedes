from helpers import job, manifest, plan_text

from ark import brief, run
from ark.plan import load_plan

APPROVAL = {"repo": "demo", "slug": "notes-edit", "plan": "/x/plan.md", "sha256": "ab" * 32,
            "approved_at": "2026-11-05T10:00:00+00:00"}


def setup(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text())
    plan = load_plan(p)
    st = run.new_state(APPROVAL)
    for mj in plan.manifest["jobs"]:
        st["jobs"][mj["id"]] = run.new_job(mj)
    return st, plan, {j["id"]: j for j in plan.manifest["jobs"]}


def test_worker_brief_has_only_its_job(tmp_path):
    st, plan, mj = setup(tmp_path)
    b = brief.worker_brief(st, "shell", mj["shell"], plan, worktree="/wt/shell", report="/r/shell.report.md")
    assert "src/shell/**" in b and "### T1 — step" in b and "### T2 — step" in b
    assert "### T3" not in b and "src/ui/**" not in b
    assert "/r/shell.report.md" in b and "STATUS: DONE" in b and "DECISIONS" in b
    assert "```" not in b


def test_worker_brief_adds_note_and_previous_report(tmp_path):
    st, plan, mj = setup(tmp_path)
    b = brief.worker_brief(st, "shell", mj["shell"], plan, worktree="/wt", report="/r",
                           note="use the store", previous_report="## What I did\n\nhalf of it")
    assert "use the store" in b and "half of it" in b


def test_reviewer_brief_asks_for_one_json_line(tmp_path):
    st, plan, mj = setup(tmp_path)
    b = brief.reviewer_brief(st, "ui", mj["ui"], plan, base="origin/ark/notes-edit", head="abc123")
    assert "git diff origin/ark/notes-edit...abc123" in b and '"verdict": "CLEAN"' in b
    assert "Includes" in b and "```" not in b


def test_worker_status():
    assert brief.worker_status("all good\nSTATUS: DONE") == ("DONE", "")
    assert brief.worker_status("x\nSTATUS: FAILED — tests red") == ("FAILED", "tests red")
    assert brief.worker_status("STATUS: BLOCKED - which store?") == ("BLOCKED", "which store?")
    assert brief.worker_status("no status")[0] == "FAILED"


def test_parse_json_line():
    assert brief.parse_json_line('text\n{"verdict": "CLEAN", "findings": []}') == {"verdict": "CLEAN", "findings": []}
    assert brief.parse_json_line('`{"action": "retry"}`') == {"action": "retry"}
    assert brief.parse_json_line("nothing") is None


def test_judge_prompt_lists_allowed_actions(tmp_path):
    st, plan, mj = setup(tmp_path)
    j = st["jobs"]["shell"]
    j["failure"] = "job gate red"
    p = brief.judge_prompt(st, "shell", j, mj["shell"], ["block", "retry"], "## What I did\n\nx")
    assert "retry, block" in p or "block, retry" in p
    assert "job gate red" in p and '"action"' in p


def test_decisions_prompt_and_fallback(tmp_path):
    st, plan, mj = setup(tmp_path)
    st["jobs"]["shell"]["status"] = "merged"
    st["jobs"]["ui"].update(status="blocked", reason="integration gate failed twice")
    reports = {"shell": "## DECISIONS\n\n- Kept notes in one file. Why: simpler.\n\n## Open issues\n\nnone"}
    p = brief.decisions_prompt(st, reports)
    assert "Kept notes in one file" in p and "<details><summary>más</summary>" in p and "```" not in p
    f = brief.fallback_decisions(st, reports)
    assert f.startswith("# Decisions — notes-edit")
    assert "Kept notes in one file" in f and "integration gate failed twice" in f and "Open issues" not in f


def test_pr_bodies(tmp_path):
    st, plan, mj = setup(tmp_path)
    st["jobs"]["shell"].update(status="merged", pr={"number": 1, "url": "u1"})
    assert "shell work" in brief.pr_body(st, "shell", mj["shell"], "## What I did\n\nx")
    body = brief.final_pr_body(st)
    assert body.startswith("**Decisions:** `docs/decisions/notes-edit.md`")
    assert "| shell | merged | u1 |" in body and "| ui | pending | - |" in body
