import json
from ark import approve, desk, paths, validate, versions
from ark.plan import sha256_file
from helpers import plan_text


def test_approval_points_at_the_frozen_snapshot(make_feature):
    p = make_feature()
    rec = approve.approve_plan(p, expect_sha=sha256_file(p))
    on_disk = json.loads(paths.approval_file("demo", "notes-edit").read_text())
    snap = paths.approvals_dir() / "demo" / "notes-edit" / "v1" / "plan.md"
    assert on_disk["plan"] == str(snap.resolve()) == rec["plan"]
    assert on_disk["desk_plan"] == str(p.resolve())
    assert on_disk["version"] == 1 and on_disk["sha256"] == sha256_file(snap) == sha256_file(p)


def test_approving_again_makes_v2(make_feature):
    p = make_feature()
    approve.approve_plan(p)
    p.write_text(plan_text().replace("Edit and delete notes.", "Edit notes."))
    rec = approve.approve_plan(p)
    assert rec["version"] == 2
    assert [v["version"] for v in versions.versions("demo", "notes-edit")] == [1, 2]


def test_a_versioned_approval_validates_the_desk_plan(make_feature):
    p = make_feature()
    approve.approve_plan(p)
    assert validate.validate(p, approved=True) == []
    snap = paths.approvals_dir() / "demo" / "notes-edit" / "v1" / "plan.md"
    assert validate.validate(snap, approved=True) == []


def test_desk_says_ok_then_stale_after_an_edit(make_feature):
    p = make_feature()
    approve.approve_plan(p)
    assert desk._approval("demo", "notes-edit", p) == "ok"
    p.write_text(p.read_text() + "\n<!-- edit -->\n")
    assert desk._approval("demo", "notes-edit", p) == "stale"
