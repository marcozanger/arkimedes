from ark.validate import validate
from helpers import job, manifest, plan_text


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
