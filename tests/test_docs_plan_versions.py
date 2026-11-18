from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docs_mention_replan_and_diff():
    doc = (ROOT / "docs" / "plan-versions.md").read_text()
    for word in ("ark replan", "ark diff", "versions.jsonl", "follow-up", "v1.legacy.md"):
        assert word in doc
    assert "ark replan" in (ROOT / "skills" / "ark-plan" / "SKILL.md").read_text()
    claude = (ROOT / "CLAUDE.md").read_text()
    assert "`ark replan <repo>/<slug>`" in claude and "`ark diff <repo>/<slug> [vA vB]`" in claude
