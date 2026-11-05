import pytest

from ark.plan import PlanError, load_plan, set_status, sha256_file, split_frontmatter, split_sections
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
