import pytest
from helpers import approve, manifest, plan_text

from ark import drafts


def _feature(ark_home, slug, spec=None, plan=None, repo="r"):
    d = ark_home / "desk" / repo / slug
    d.mkdir(parents=True)
    if spec:
        (d / "spec.md").write_text(f"---\nstatus: {spec}\nrepo: {repo}\nslug: {slug}\ncreated: 2026-11-05\n---\n# Spec {slug}\n\n## Problem\n\nx\n")
    if plan is not None:
        (d / "plan.md").write_text(plan)
    return d


@pytest.fixture
def sample_desk(ark_home, make_repo):
    make_repo("r")
    _feature(ark_home, "only-spec", spec="draft")
    _feature(ark_home, "ready", spec="approved", plan=plan_text(repo="r", slug="ready"))
    _feature(ark_home, "broken", spec="approved", plan=plan_text(repo="r", slug="broken", drop=("Goal",)))
    d = _feature(ark_home, "done-one", spec="approved", plan=plan_text(repo="r", slug="done-one"))
    approve(d / "plan.md", "r", "done-one")
    _feature(ark_home, "plan-only", plan=plan_text(repo="r", slug="plan-only", spec=None))
    return ark_home


def test_drafts_list(sample_desk):
    got = {(d["repo"], d["slug"]): d for d in drafts.list_drafts()}
    assert set(got) == {("r", "only-spec"), ("r", "ready"), ("r", "broken"), ("r", "plan-only")}
    a = got[("r", "only-spec")]
    assert a["spec"]["status"] == "draft" and a["plan"] is None and a["next"] == "review spec"
    b = got[("r", "ready")]
    assert b["spec"]["status"] == "approved" and b["plan"]["problems"] == []
    assert b["plan"]["estimate"]["total_usd"] > 0 and b["plan"]["estimate"]["end_to_end_minutes"] > 0
    assert b["next"] == "review plan"
    c = got[("r", "broken")]
    assert c["plan"]["problems"] and c["plan"]["estimate"] is None and c["next"] == "fix plan"
    assert got[("r", "plan-only")]["spec"] is None


def test_draft_detail(sample_desk):
    d = drafts.detail("r", "ready")
    assert d["spec"]["text"].startswith("---") and d["spec"]["review"]["sections"]
    assert len(d["plan"]["sha256"]) == 64


def test_bad_names_are_refused(sample_desk):
    for repo, slug in (("..", "x"), ("r", "../x"), ("r", "a/b"), ("r", "missing")):
        with pytest.raises(drafts.DraftError):
            drafts.detail(repo, slug)


def test_images(sample_desk):
    (sample_desk / "desk" / "r" / "ready" / "a.png").write_bytes(b"png")
    assert drafts.images("r", "ready", "a.png") == b"png"
    for name in ("../a.png", "spec.md", "nope.png"):
        with pytest.raises(drafts.DraftError):
            drafts.images("r", "ready", name)
