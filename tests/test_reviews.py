import pytest

from ark import reviews

DOC = "---\nstatus: draft\n---\n# T\n\n## Problem\n\nx\n\n```\n## not a heading\n```\n\n## Goal\n\ny\n\n## Goal\n\nz\n"


@pytest.fixture
def desk_feature(ark_home):
    d = ark_home / "desk" / "r" / "s"
    d.mkdir(parents=True)
    (d / "spec.md").write_text(DOC)
    return d


def test_sections_by_heading_with_fences_and_repeats():
    s = reviews.sections(DOC)
    assert [x["key"] for x in s] == ["problem", "goal", "goal-2"]
    assert [x["title"] for x in s] == ["Problem", "Goal", "Goal"]
    assert all(len(x["hash"]) == 64 for x in s)


def test_save_and_load(desk_feature):
    d = desk_feature
    reviews.save(d, "spec", "problem", "ok", "")
    reviews.save(d, "spec", "goal", "change", "say who")
    got = {x["key"]: x for x in reviews.load(d, "spec")["sections"]}
    assert got["problem"]["verdict"] == "ok" and got["goal"]["note"] == "say who"
    assert got["goal-2"]["verdict"] is None
    assert reviews.summary(d, "spec") == {"ok": 1, "change": 1, "total": 3}


def test_an_edited_section_resets_only_itself(desk_feature):
    d = desk_feature
    reviews.save(d, "spec", "problem", "ok", "")
    reviews.save(d, "spec", "goal", "ok", "")
    (d / "spec.md").write_text(DOC.replace("y\n", "y changed\n"))
    got = {x["key"]: x["verdict"] for x in reviews.load(d, "spec")["sections"]}
    assert got == {"problem": "ok", "goal": None, "goal-2": None}


def test_a_corrupt_file_reads_as_empty_with_a_warning(desk_feature):
    (desk_feature / "review.json").write_text("{not json")
    r = reviews.load(desk_feature, "spec")
    assert r["warning"] and all(x["verdict"] is None for x in r["sections"])
    reviews.save(desk_feature, "spec", "problem", "ok", "")
    assert reviews.load(desk_feature, "spec")["warning"] is None


def test_unknown_section_or_verdict_is_refused(desk_feature):
    with pytest.raises(reviews.ReviewError):
        reviews.save(desk_feature, "spec", "nope", "ok", "")
    with pytest.raises(reviews.ReviewError):
        reviews.save(desk_feature, "spec", "problem", "maybe", "")


def test_clearing_and_missing_doc(desk_feature):
    reviews.save(desk_feature, "spec", "problem", "ok", "")
    reviews.save(desk_feature, "spec", "problem", None, "")
    assert reviews.summary(desk_feature, "spec")["ok"] == 0
    with pytest.raises(reviews.ReviewError):
        reviews.save(desk_feature, "plan", "problem", "ok", "")
    with pytest.raises(reviews.ReviewError):
        reviews.save(desk_feature, "spec", "problem", "ok", "x" * 4001)
