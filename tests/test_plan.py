import pytest

from ark.plan import PlanError, load_plan, task_blocks, task_groups, task_texts, set_status, sha256_file, split_frontmatter, split_sections
from helpers import plan_text


def test_frontmatter_and_body():
    fm, body = split_frontmatter("---\nstatus: draft\nrepo: demo\n---\n# T\n")
    assert fm == {"status": "draft", "repo": "demo"}
    assert body == "# T\n"


def test_no_frontmatter_is_empty():
    assert split_frontmatter("# T\n") == ({}, "# T\n")


def test_frontmatter_must_be_a_mapping():
    with pytest.raises(PlanError):
        split_frontmatter("---\n- a\n---\n")


def test_sections_ignore_headings_inside_fences():
    secs = split_sections("## A\none\n```\n## not a section\n```\n## B\ntwo\n")
    assert list(secs) == ["A", "B"]
    assert "## not a section" in secs["A"]


def test_load_plan_reads_tasks_and_manifest(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text())
    plan = load_plan(p)
    assert plan.tasks == ["T1", "T2", "T3"]
    assert [j["id"] for j in plan.manifest["jobs"]] == ["shell", "ui"]
    assert plan.manifest_error is None


def test_manifest_missing_block(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text().replace("```yaml", "```text"))
    assert "no ```yaml block" in load_plan(p).manifest_error


def test_set_status_only_touches_frontmatter(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text() + "\nstatus: draft stays here\n")
    set_status(p, "closed")
    assert load_plan(p).frontmatter["status"] == "closed"
    assert "status: draft stays here" in p.read_text()


def test_sha256_changes_with_content(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("a")
    first = sha256_file(p)
    p.write_text("b")
    assert first != sha256_file(p)


from ark.plan import load_plan, task_texts


def test_task_texts_by_id(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("---\nstatus: draft\n---\n# X\n\n## Tasks\n\nintro\n\n### T1 — first\n\nDo one.\n\n"
                 "### T2 — second\n\nDo two.\n\n## PR plan\n\nx\n")
    t = task_texts(load_plan(p))
    assert list(t) == ["T1", "T2"]
    assert t["T1"] == "### T1 — first\n\nDo one."
    assert t["T2"].startswith("### T2 — second") and "Do two." in t["T2"]


GROUPED = "\n".join([
    "<!-- fold -->", "",
    "### shell — feat(shell): notes.delete IPC", "",
    "#### T1 — handler", "", "Do one.", "", "##### Steps", "", "a step", "",
    "#### T2 — test", "", "Do two.", "",
    "### ui — feat(ui): delete button", "",
    "#### T3 — button", "", "Do three.",
])

FLAT = "\n".join([
    "intro", "",
    "### T1 — first", "", "Do one.", "", "#### Files", "", "a.py", "",
    "### T2 — second", "", "Do two.",
])


def test_grouped_task_blocks_end_at_the_next_group():
    b = task_blocks(GROUPED)
    assert [(x.id, x.level, x.group) for x in b] == [
        ("T1", 4, "shell"), ("T2", 4, "shell"), ("T3", 4, "ui")]
    assert b[0].text == "#### T1 — handler\n\nDo one.\n\n##### Steps\n\na step"
    assert b[1].text == "#### T2 — test\n\nDo two."
    assert task_groups(GROUPED) == ["shell", "ui"]


def test_flat_task_blocks_keep_todays_text():
    b = task_blocks(FLAT)
    assert [(x.id, x.level, x.group) for x in b] == [("T1", 3, None), ("T2", 3, None)]
    assert b[0].text == "### T1 — first\n\nDo one.\n\n#### Files\n\na.py"
    assert task_groups(FLAT) == []


def test_load_plan_reads_grouped_tasks(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("---\nstatus: draft\n---\n# X\n\n## Tasks\n\n" + GROUPED + "\n\n## PR plan\n\nx\n")
    plan = load_plan(p)
    assert plan.tasks == ["T1", "T2", "T3"]
    assert [b.group for b in plan.blocks] == ["shell", "shell", "ui"]
    t = task_texts(plan)
    assert t["T3"] == "#### T3 — button\n\nDo three."


def test_helper_plan_is_layered_and_flat_on_request(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text())
    plan = load_plan(p)
    assert list(plan.sections) == ["Goal", "Context", "PR plan", "Decisions", "Tasks", "Run manifest"]
    assert plan.sections["Tasks"].startswith("<!-- fold -->")
    assert [(b.id, b.group) for b in plan.blocks] == [("T1", "shell"), ("T2", "shell"), ("T3", "ui")]
    p.write_text(plan_text(flat=True))
    flat = load_plan(p)
    assert [(b.id, b.level) for b in flat.blocks] == [("T1", 3), ("T2", 3), ("T3", 3)]
    assert "Decisions" not in flat.sections
