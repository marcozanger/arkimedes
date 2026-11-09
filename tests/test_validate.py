from ark.validate import validate
from helpers import approve, job, manifest, plan_text, set_accounts


def checks(path, **kw):
    return sorted({p.check for p in validate(path, **kw)})


def messages(path, **kw):
    return " | ".join(str(p) for p in validate(path, **kw))


def test_valid_plan_has_no_problems(make_feature):
    assert validate(make_feature()) == []


# 1. frontmatter and repo

def test_missing_frontmatter_key(make_feature):
    assert "missing `created`" in messages(make_feature(plan_text(created=None)))


def test_slug_must_match_folder(make_feature):
    assert "slug is 'other'" in messages(make_feature(plan_text(slug="other")))


def test_repo_without_origin(make_feature, make_repo):
    make_repo("local-only", origin=False)
    p = make_feature(plan_text(repo="local-only"), repo="local-only", create_repo=False)
    assert "repo" in checks(p)


def test_unknown_repo(make_feature):
    p = make_feature(plan_text(repo="nope"), repo="nope", create_repo=False)
    assert "repo" in checks(p)


def test_account_that_cannot_be_used(make_feature, ark_home, tmp_path):
    set_accounts(ark_home, [{"match": "demo", "claude_config_dir": str(tmp_path / "nope")}])
    p = make_feature()
    assert checks(p) == ["account"]
    assert "does not exist" in messages(p)


def test_account_that_works_is_fine(make_feature, ark_home, tmp_path):
    (tmp_path / "cw").mkdir()
    set_accounts(ark_home, [{"match": "demo", "claude_config_dir": str(tmp_path / "cw")}])
    assert validate(make_feature()) == []


def test_repo_three_levels_under_a_root(make_feature):
    p = make_feature(plan_text(repo="gl/proposals/v0"), repo="gl/proposals/v0")
    assert validate(p) == []


# spec

def test_spec_must_be_approved(make_feature):
    assert "spec" in checks(make_feature(spec_status="draft"))


def test_no_spec_needs_spec_none(make_feature):
    assert "spec" in checks(make_feature(spec_status=None))
    assert validate(make_feature(plan_text(spec="none"), spec_status=None)) == []


def test_spec_path_inside_the_repo(make_feature, make_repo):
    repo = make_repo("demo")
    (repo / "docs").mkdir()
    (repo / "docs/s.md").write_text("x")
    assert validate(make_feature(plan_text(spec="docs/s.md"), spec_status=None, create_repo=False)) == []
    assert "spec" in checks(make_feature(plan_text(spec="docs/missing.md"), spec_status=None, create_repo=False))


# structure

def test_missing_section(make_feature):
    p = make_feature(plan_text(drop=("PR plan",)))
    assert "missing or empty section `## PR plan`" in messages(p)


def test_manifest_needs_integration_gate(make_feature):
    m = manifest()
    del m["integration_gate"]
    assert "missing `integration_gate`" in messages(make_feature(plan_text(m)))


# 2. tasks

def test_task_in_no_job(make_feature):
    assert "T4 is in no job" in messages(make_feature(plan_text(tasks=("T1", "T2", "T3", "T4"))))


def test_task_in_two_jobs(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]),
                       job("ui", ["T2", "T3"], ["src/ui/**"], after=["shell"])])
    assert "T2 is in more than one job" in messages(make_feature(plan_text(m)))


def test_job_names_a_task_that_does_not_exist(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2", "T9"], ["src/shell/**"]),
                       job("ui", ["T3"], ["src/ui/**"], after=["shell"])])
    assert "task T9 is not under" in messages(make_feature(plan_text(m)))


# 3. job fields

def test_job_without_done(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"], done=[]),
                       job("ui", ["T3"], ["src/ui/**"], after=["shell"])])
    assert "shell: missing `done`" in messages(make_feature(plan_text(m)))


def test_bad_tier_and_budget(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2", "T3"], ["src/**"], tier="T5", budget={"minutes": 10})])
    msg = messages(make_feature(plan_text(m)))
    assert "tier must be" in msg
    assert "budget needs" in msg


def test_job_id_must_be_branch_safe(make_feature):
    m = manifest(jobs=[job("Shell IPC", ["T1", "T2", "T3"], ["src/**"])])
    assert "id 'Shell IPC'" in messages(make_feature(plan_text(m)))


# 4. after

def test_after_unknown_job(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]),
                       job("ui", ["T3"], ["src/ui/**"], after=["nope"])])
    assert "unknown job 'nope'" in messages(make_feature(plan_text(m)))


def test_after_cycle(make_feature):
    m = manifest(jobs=[job("a", ["T1"], ["a/**"], after=["c"]),
                       job("b", ["T2"], ["b/**"], after=["a"]),
                       job("c", ["T3"], ["c/**"], after=["b"])])
    assert "cycle:" in messages(make_feature(plan_text(m)))


# 5. areas

def test_parallel_jobs_with_overlapping_areas(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/**"]), job("ui", ["T3"], ["src/ui/**"])])
    assert "shell and ui may run at the same time" in messages(make_feature(plan_text(m)))


def test_overlap_is_fine_when_ordered(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/**"]),
                       job("ui", ["T3"], ["src/ui/**"], after=["shell"])])
    assert validate(make_feature(plan_text(m))) == []


def test_overlap_is_fine_through_a_transitive_order(make_feature):
    m = manifest(jobs=[job("a", ["T1"], ["src/**"]),
                       job("b", ["T2"], ["b/**"], after=["a"]),
                       job("c", ["T3"], ["src/c/**"], after=["b"])])
    assert validate(make_feature(plan_text(m))) == []


# 6. approval (the rest is in test_cli.py)

def test_approved_check_needs_a_closed_plan(make_feature):
    assert "plan is not closed" in messages(make_feature(), approved=True)


# fix-up: wrong-typed or undecodable input gives problems, never a traceback

def test_unhashable_tier_is_a_problem(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2", "T3"], ["src/**"], tier=["T1"])])
    assert "tier must be" in messages(make_feature(plan_text(m)))


def test_approval_file_that_is_not_an_object(make_feature, ark_home):
    p = make_feature(plan_text(status="closed"))
    f = ark_home / "approvals/demo/notes-edit.json"
    f.parent.mkdir(parents=True)
    f.write_text("[]")
    assert "approval file unreadable" in messages(p, approved=True)


def test_spec_md_that_is_a_directory_or_not_utf8(make_feature):
    p = make_feature(spec_status=None)
    (p.parent / "spec.md").mkdir()
    assert "spec" in checks(p)
    (p.parent / "spec.md").rmdir()
    (p.parent / "spec.md").write_bytes(b"---\nstatus: approved\n---\n\xff")
    assert "spec" in checks(p)
    (p.parent / "spec.md").write_bytes(b"\xff\xfe")
    assert "spec" in checks(p)


def test_plan_that_is_not_utf8(make_feature):
    p = make_feature()
    p.write_bytes(b"---\nstatus: draft\n---\n\xff\xfe")
    assert "frontmatter" in checks(p)


# fix-up: required fields must be real strings

def test_gate_must_be_a_string(make_feature):
    for bad in (False, 0, ["a"], "  "):
        m = manifest(jobs=[job("shell", ["T1", "T2", "T3"], ["src/**"], gate=bad)])
        assert "shell: `gate` must be a non-empty string" in messages(make_feature(plan_text(m))), bad


def test_other_job_strings(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2", "T3"], ["src/**"], pr_title=5, includes=["x"], excludes={"a": 1})])
    msg = messages(make_feature(plan_text(m)))
    for key in ("pr_title", "includes", "excludes"):
        assert f"shell: `{key}` must be a non-empty string" in msg


def test_manifest_strings_and_setup(make_feature):
    msg = messages(make_feature(plan_text(manifest(integration_gate=["x"], final_pr_title=3))))
    assert "`integration_gate` must be a non-empty string" in msg
    assert "`final_pr_title` must be a non-empty string" in msg
    assert "`setup` must be a list of non-empty strings" in messages(
        make_feature(plan_text(manifest(setup=[1, None]))))


# fix-up: the plan must sit on the desk

def test_plan_outside_the_desk(make_feature, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "plan.md").write_text(plan_text())
    (elsewhere / "spec.md").write_text("---\nstatus: approved\n---\n")
    make_feature()
    assert "plan is not on the desk" in messages(elsewhere / "plan.md")


def test_approval_must_point_at_this_plan(make_feature, ark_home):
    import json
    from ark.plan import sha256_file
    p = make_feature(plan_text(status="closed"))
    f = ark_home / "approvals/demo/notes-edit.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"sha256": sha256_file(p), "plan": "/elsewhere/plan.md"}))
    assert "approval record is for another plan" in messages(p, approved=True)
    f.write_text(json.dumps({"sha256": sha256_file(p), "plan": str(p.resolve())}))
    assert validate(p, approved=True) == []


import pytest


@pytest.mark.parametrize("field,value", [
    ("tier", 0), ("tier", False), ("tier", ["T1"]),
    ("done", False), ("done", 0), ("done", "text"), ("done", [1]),
    ("area", 0), ("area", False),
    ("budget", False), ("budget", 0), ("budget", "30"), ("budget", {"minutes": "x", "usd": 1}),
    ("budget", {"minutes": 0, "usd": 1}),
    ("gate", 0), ("gate", False), ("pr_title", 0), ("includes", False), ("excludes", 0),
    ("after", 0), ("after", False), ("after", "shell"),
    ("heavy", 0), ("heavy", "yes"),
    ("tasks", 0), ("tasks", False),
])
def test_falsy_or_mistyped_job_value_is_a_problem(make_feature, field, value):
    j = job("shell", ["T1", "T2", "T3"], ["src/**"])
    j[field] = value
    m = manifest(jobs=[j])
    probs = validate(make_feature(plan_text(m)))
    assert any(p.check in ("jobs", "after") and "shell" in str(p) for p in probs), (field, value)


# 7. layers (only while the plan is not closed)

def test_layered_helper_plan_is_valid(make_feature):
    assert validate(make_feature()) == []


def test_flat_draft_plan_is_rejected(make_feature):
    p = make_feature(plan_text(flat=True))
    assert checks(p) == ["layers"]
    assert "`## Decisions`" in messages(p) and "T1, T2, T3" in messages(p)


def test_flat_closed_plan_still_passes_approved(make_feature):
    p = make_feature(plan_text(flat=True))
    approve(p)
    assert validate(p, approved=True) == []
    assert validate(p) == []


def test_empty_decisions_section(make_feature):
    p = make_feature(plan_text(drop=("Decisions",)))
    assert "missing or empty section `## Decisions`" in messages(p)


def test_sections_out_of_layer_order(make_feature):
    text = plan_text()
    pr = "## PR plan\n\nShell first, then the UI.\n\n"
    text = text.replace(pr, "").replace("## Run manifest", pr + "## Run manifest")
    assert "sections must come in the order" in messages(make_feature(text))


def test_decision_missing_cons(make_feature):
    text = plan_text().replace("Cons: nothing to see until PR 2. ", "")
    msg = messages(make_feature(text))
    assert "decision 'Shell first.'" in msg and "`Cons:`" in msg


def test_decision_without_bold_choice(make_feature):
    text = plan_text().replace("- **Shell first.**", "- Shell first.")
    assert "chosen option in bold" in messages(make_feature(text))


def test_decision_labels_out_of_order(make_feature):
    text = plan_text().replace("Pros: the UI has an API to call. Cons: nothing to see until PR 2.",
                               "Cons: nothing to see until PR 2. Pros: the UI has an API to call.")
    assert "in that order" in messages(make_feature(text))


def test_tasks_need_the_fold_line(make_feature):
    text = plan_text().replace("<!-- fold -->\n\n", "")
    assert "`<!-- fold -->`" in messages(make_feature(text))


def test_group_must_be_a_job(make_feature):
    text = plan_text().replace("### ui — ", "### front — ")
    msg = messages(make_feature(text))
    assert "`### front`" in msg and "not a job id" in msg


def test_group_holding_another_jobs_task(make_feature):
    m = manifest(jobs=[job("shell", ["T1"], ["src/shell/**"]),
                       job("ui", ["T2", "T3"], ["src/ui/**"], after=["shell"])])
    text = plan_text(m).replace("### ui — feat: ui\n\n#### T2", "#### T2")
    msg = messages(make_feature(text))
    assert "group `shell` holds T1, T2, T3 but its job lists T1" in msg
