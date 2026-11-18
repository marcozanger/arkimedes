import json
from ark import paths, versions
from ark.plan import set_status, sha256_file
from helpers import job, manifest, plan_text


def _closed(make_feature, m=None, **kw):
    p = make_feature(plan_text(m, **kw))
    set_status(p, "closed")
    return p


def test_the_first_freeze_is_v1_with_plan_and_spec(make_feature):
    p = _closed(make_feature)
    rec = versions.freeze(p, "2026-11-12T10:00:00+00:00")
    assert rec["version"] == 1
    d = paths.approvals_dir() / "demo" / "notes-edit" / "v1"
    assert (d / "plan.md").read_text() == p.read_text()
    assert (d / "spec.md").read_text() == (p.parent / "spec.md").read_text()
    assert rec["plan"] == str((d / "plan.md").resolve()) and rec["sha256"] == sha256_file(p)
    assert [v["version"] for v in versions.versions("demo", "notes-edit")] == [1]


def test_a_second_freeze_is_v2_and_keeps_v1(make_feature):
    p = _closed(make_feature)
    versions.freeze(p, "2026-11-12T10:00:00+00:00")
    p.write_text(p.read_text() + "\n<!-- v2 -->\n")
    rec = versions.freeze(p, "2026-11-12T11:00:00+00:00")
    assert rec["version"] == 2
    assert "<!-- v2 -->" not in versions.text("demo", "notes-edit", 1)
    assert "<!-- v2 -->" in versions.text("demo", "notes-edit", 2)


def test_no_json_file_is_written_next_to_the_approval(make_feature):
    versions.freeze(_closed(make_feature), "2026-11-12T10:00:00+00:00")
    assert not list((paths.approvals_dir() / "demo" / "notes-edit").rglob("*.json"))


def test_a_legacy_approval_becomes_v1_from_its_kept_copy(make_feature):
    p = _closed(make_feature)
    old = sha256_file(p)
    f = paths.approval_file("demo", "notes-edit")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"repo": "demo", "slug": "notes-edit", "plan": str(p.resolve()),
                             "sha256": old, "approved_at": "2026-11-01T10:00:00+00:00"}))
    (p.parent / "v1.legacy.md").write_text(p.read_text())
    p.write_text(p.read_text() + "\n<!-- v2 -->\n")
    rec = versions.freeze(p, "2026-11-12T10:00:00+00:00")
    vs = versions.versions("demo", "notes-edit")
    assert [v["version"] for v in vs] == [1, 2] and rec["version"] == 2
    assert vs[0]["kept"] is True and vs[0]["sha256"] == old and vs[0]["approved_at"] == "2026-11-01T10:00:00+00:00"


def test_a_legacy_approval_with_no_copy_is_v1_not_kept(make_feature):
    p = _closed(make_feature)
    f = paths.approval_file("demo", "notes-edit")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"repo": "demo", "slug": "notes-edit", "plan": str(p.resolve()),
                             "sha256": "a" * 64, "approved_at": "2026-11-01T10:00:00+00:00"}))
    versions.freeze(p, "2026-11-12T10:00:00+00:00")
    v1 = versions.versions("demo", "notes-edit")[0]
    assert v1["kept"] is False and versions.text("demo", "notes-edit", 1) is None


def _two(make_feature, m1, m2, tasks2=None):
    p = _closed(make_feature, m1)
    a = versions.freeze(p, "2026-11-12T10:00:00+00:00")
    p.write_text(plan_text(m2, status="closed") if tasks2 is None else tasks2)
    b = versions.freeze(p, "2026-11-12T11:00:00+00:00")
    return a["plan"], b["plan"]


def test_job_changes_by_id_entry_and_task_text(make_feature):
    m1 = manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**"]), job("c", ["T3"], ["c/**"])])
    m2 = manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**", "b2/**"]), job("d", ["T3"], ["d/**"])])
    old, new = _two(make_feature, m1, m2)
    assert versions.job_changes(old, new) == {"a": "unchanged", "b": "changed", "c": "removed", "d": "added"}


def test_an_edited_task_text_changes_its_job_only(make_feature):
    m = manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2", "T3"], ["b/**"])])
    p = _closed(make_feature, m)
    old = versions.freeze(p, "2026-11-12T10:00:00+00:00")["plan"]
    p.write_text(p.read_text().replace("#### T2 — step\n\nDo it, test first.", "#### T2 — step\n\nDo it twice."))
    new = versions.freeze(p, "2026-11-12T11:00:00+00:00")["plan"]
    assert versions.job_changes(old, new) == {"a": "unchanged", "b": "changed"}


def test_no_old_text_means_every_shared_job_changed(make_feature):
    p = _closed(make_feature)
    new = versions.freeze(p, "2026-11-12T10:00:00+00:00")["plan"]
    assert versions.job_changes(None, new) == {"shell": "changed", "ui": "changed"}


def test_diff_shows_the_spec_first_then_the_plan(make_feature):
    p = _closed(make_feature)
    versions.freeze(p, "2026-11-12T10:00:00+00:00")
    (p.parent / "spec.md").write_text((p.parent / "spec.md").read_text() + "New why.\n")
    p.write_text(p.read_text().replace("Edit and delete notes.", "Edit, delete and pin notes."))
    versions.freeze(p, "2026-11-12T11:00:00+00:00")
    out = versions.diff("demo", "notes-edit", 1, 2)
    assert out.index("spec.md") < out.index("plan.md")
    assert "+New why." in out and "-Edit and delete notes." in out and "+Edit, delete and pin notes." in out


def test_job_diff_holds_only_that_job(make_feature):
    m1 = manifest(jobs=[job("a", ["T1"], ["a/**"]), job("b", ["T2"], ["b/**"])])
    m2 = manifest(jobs=[job("a", ["T1"], ["a/**", "a2/**"]), job("b", ["T2"], ["b/**"])])
    old, new = _two(make_feature, m1, m2)
    d = versions.job_diff(old, new, "a")
    assert "+- a2/**" in d or "a2/**" in d
    assert "b/**" not in d
