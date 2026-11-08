import subprocess
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
GONE = ["bin/planner", "hooks/desk-start.sh", "hooks/desk-statusline.sh", ".claude/settings.json"]


def test_the_desk_files_are_gone():
    assert [f for f in GONE if (CODE / f).exists()] == []


def test_nothing_still_points_to_them():
    r = subprocess.run(["git", "grep", "-n", "-e", "planner", "-e", "desk-start", "-e", "desk-statusline",
                        "--", ".", ":!specs", ":!docs/decisions", ":!tests/test_desk_retired.py"],
                       cwd=CODE, capture_output=True, text=True)
    assert r.stdout == ""


def test_claude_md_sends_planning_to_the_skill():
    text = (CODE / "CLAUDE.md").read_text()
    assert "/ark-plan" in text and "desk mode" not in text
