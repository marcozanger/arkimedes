"""The control center docs, CLAUDE.md and the ark-plan skill say what the page writes and how a resume works."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(p):
    return (ROOT / p).read_text(encoding="utf-8")


def test_control_center_doc_names_the_writes_and_the_rule():
    t = _read("docs/control-center.md")
    for word in ("review.json", "X-Ark-Token", "Origin", "/api/review", "/api/approve-spec",
                 "/api/approve-plan", "/api/answer"):
        assert word in t, word


def test_claude_md_says_who_approves():
    t = " ".join(_read("CLAUDE.md").split())
    assert "from a terminal or with a click in `ark ui`" in t
    assert "a session never approves on its own" in t


def test_skill_resumes_from_review_json():
    t = " ".join(_read("skills/ark-plan/SKILL.md").split())
    assert "Read `review.json` when present" in t
    assert "apply those first" in t
