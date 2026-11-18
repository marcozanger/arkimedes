import json

from ark import approve, cli, paths, versions
from ark.plan import load_plan
from helpers import job, manifest, plan_text


def test_replan_reopens_the_plan_as_a_draft(make_feature, capsys):
    p = make_feature()
    approve.approve_plan(p)
    assert cli.main(["replan", "demo/notes-edit"]) == 0
    assert load_plan(p).frontmatter["status"] == "draft"
    assert "v2" in capsys.readouterr().out
    assert (paths.approvals_dir() / "demo" / "notes-edit" / "v1" / "plan.md").is_file()  # untouched


def test_replan_of_a_legacy_approval_keeps_its_text(make_feature):
    p = make_feature()
    from helpers import approve as legacy_approve
    legacy_approve(p)
    closed = p.read_text()
    assert cli.main(["replan", "demo/notes-edit"]) == 0
    assert (p.parent / versions.LEGACY_PLAN).read_text() == closed
    assert (p.parent / versions.LEGACY_SPEC).read_text() == (p.parent / "spec.md").read_text()


def test_replan_refuses_a_plan_never_approved(make_feature, capsys):
    make_feature()
    assert cli.main(["replan", "demo/notes-edit"]) == 1
    assert "not approved" in capsys.readouterr().err


def test_replan_writes_nothing_under_approvals(make_feature):
    p = make_feature()
    approve.approve_plan(p)
    before = sorted(x.name for x in paths.approvals_dir().rglob("*"))
    cli.main(["replan", "demo/notes-edit"])
    assert sorted(x.name for x in paths.approvals_dir().rglob("*")) == before


def _v2(make_feature):
    p = make_feature()
    approve.approve_plan(p)
    p.write_text(p.read_text().replace("Edit and delete notes.", "Edit notes.").replace("status: closed", "status: draft"))
    approve.approve_plan(p)
    return p


def test_diff_defaults_to_the_last_two_versions(make_feature, capsys):
    _v2(make_feature)
    assert cli.main(["diff", "demo/notes-edit"]) == 0
    out = capsys.readouterr().out
    assert "v1" in out and "v2" in out and "-Edit and delete notes." in out and "+Edit notes." in out
    assert out.index("spec.md") < out.index("plan.md")


def test_diff_takes_two_versions(make_feature, capsys):
    _v2(make_feature)
    assert cli.main(["diff", "demo/notes-edit", "v2", "v1"]) == 0
    assert "+Edit and delete notes." in capsys.readouterr().out


def test_diff_with_one_version_says_so(make_feature, capsys):
    p = make_feature()
    approve.approve_plan(p)
    assert cli.main(["diff", "demo/notes-edit"]) == 1
    assert "only one version" in capsys.readouterr().err


def test_plans_shows_the_version(make_feature, capsys):
    _v2(make_feature)
    cli.main(["plans"])
    line = [l for l in capsys.readouterr().out.splitlines() if "demo/notes-edit" in l][0]
    assert " v2 " in f" {line} "


def test_change_table_per_job(make_feature):
    from ark import run
    p = make_feature(plan_text(manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**"]),
                                              job("c", ["T3"], ["c/**"])])))
    approve.approve_plan(p)
    st = run.new_state(json.loads(paths.approval_file("demo", "notes-edit").read_text()))
    for mj in load_plan(versions.plan_path("demo", "notes-edit", 1)).manifest["jobs"]:
        st["jobs"][mj["id"]] = run.new_job(mj)
    for jid, status in {"a": "merged", "b": "merged", "c": "working"}.items():
        st["jobs"][jid]["status"] = status
    run.save(st)
    p.write_text(plan_text(manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**", "x/**"]),
                                          job("d", ["T3"], ["d/**"])])))
    table = cli._change_table(p)
    assert "a" in table and "unchanged" in table
    assert "merged in v1, re-runs as a follow-up" in table
    assert "removed" in table and "added" in table
