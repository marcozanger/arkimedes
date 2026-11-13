import json

import pytest

from ark import euklides

TODAY = {
    "model": "opus", "usd_per_call": 3, "minutes_per_call": 20, "tools": ["Read", "Grep", "Glob"],
    "denied_tools": ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent"],
    "budget_margin": 0.10, "decisions_per_job": 4,
}


def write(ark_home, block):
    f = ark_home / "workspace.json"
    data = json.loads(f.read_text())
    data["euklides"] = block
    f.write_text(json.dumps(data))


def test_no_block_gives_todays_values(ark_home):
    assert euklides.settings() == TODAY


@pytest.mark.parametrize("key,value", [
    ("model", "sonnet"), ("model", "claude-sonnet-5-5"), ("usd_per_call", 1.5), ("minutes_per_call", 5),
    ("tools", ["Read"]), ("denied_tools", ["Bash"]), ("budget_margin", 0), ("budget_margin", 1),
    ("decisions_per_job", 0),
])
def test_each_key_overrides_only_itself(ark_home, key, value):
    write(ark_home, {key: value})
    assert euklides.settings() == {**TODAY, key: value}


@pytest.mark.parametrize("key,value", [
    ("model", 5), ("model", "gpt"), ("model", ""), ("usd_per_call", 0), ("usd_per_call", "3"), ("usd_per_call", True),
    ("minutes_per_call", -1), ("minutes_per_call", None), ("tools", "Read"), ("tools", [""]), ("tools", [1]),
    ("denied_tools", "Bash"), ("denied_tools", [" "]), ("budget_margin", 1.5), ("budget_margin", -0.1),
    ("budget_margin", "x"), ("decisions_per_job", -1), ("decisions_per_job", 1.5), ("decisions_per_job", True),
    ("bogus", 1),
])
def test_a_bad_value_names_its_key(ark_home, key, value):
    write(ark_home, {key: value})
    with pytest.raises(ValueError, match=f"euklides.{key}"):
        euklides.settings()


def test_a_tool_in_both_lists_is_refused(ark_home):
    write(ark_home, {"tools": ["Read", "Bash"]})
    with pytest.raises(ValueError, match="euklides.tools"):
        euklides.settings()
