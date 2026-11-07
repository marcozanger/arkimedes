import re
from pathlib import Path

import yaml

from ark import cli

SKILL = Path(__file__).resolve().parents[1] / "skills" / "ark-plan" / "SKILL.md"


def test_frontmatter_names_the_skill_and_says_when_to_use_it():
    text = SKILL.read_text()
    fm = yaml.safe_load(text.split("---\n")[1])
    assert fm["name"] == "ark-plan"
    assert "/ark-plan" in fm["description"] and "/planear" in fm["description"]


def test_every_ark_command_it_names_exists(capsys):
    named = set(re.findall(r"`ark (\w+)", SKILL.read_text()))
    assert {"status", "validate", "approve", "estimate"} <= named
    for cmd in named:
        try:
            cli.main([cmd, "--help"])
        except SystemExit as e:
            assert e.code == 0, f"ark {cmd} is not a subcommand"
    capsys.readouterr()


def test_it_carries_the_desk_rules():
    text = SKILL.read_text()
    for rule in ("approvals/", "runs/", "history/", "repos/", "stale", "Spanish", "## Presupuesto"):
        assert rule in text
