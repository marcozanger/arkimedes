from ark import brief
from ark.plan import load_plan
from helpers import plan_text


def _brief(make_feature, job_state):
    p = make_feature()
    plan = load_plan(p)
    mj = plan.manifest["jobs"][0]
    st = {"repo": "demo", "slug": "notes-edit", "jobs": {mj["id"]: job_state}}
    return brief.worker_brief(st, mj["id"], mj, plan, worktree="/tmp/w", report="/tmp/r.md")


def test_a_follow_up_brief_says_main_has_the_previous_work(make_feature):
    text = _brief(make_feature, {"followup": {"from_version": 1, "diff": "-old line\n+new line"}})
    assert "follow-up" in text and "v1" in text
    assert "+new line" in text
    assert "only what is missing" in text


def test_a_normal_brief_has_no_follow_up_section(make_feature):
    assert "follow-up" not in _brief(make_feature, {})
