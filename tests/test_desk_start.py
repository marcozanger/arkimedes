import json
import os
import subprocess
from pathlib import Path

from ark.validate import validate

CODE = Path(__file__).resolve().parents[1]


def run_hook(ark_home, **extra):
    env = {**os.environ, "ARK_HOME": str(ark_home)}
    env.pop("ARK_ROLE", None)
    env.update(extra)
    return subprocess.run([str(CODE / "hooks/desk-start.sh")], input="{}", capture_output=True,
                          text=True, env=env)


def test_desk_start_injects_the_overview(make_feature, ark_home):
    make_feature()
    r = run_hook(ark_home)
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("# Desk mode")
    assert "notes-edit" in ctx and "ark approve" in ctx


def test_desk_start_is_silent_for_runner_processes(ark_home):
    assert run_hook(ark_home, ARK_ROLE="tick").stdout == ""


def test_the_plan_template_meets_the_contract_once_filled(make_feature):
    text = (CODE / "templates/plan.md").read_text()
    text = text.replace("repo: humanharness", "repo: demo")
    assert validate(make_feature(text, spec_status=None)) == []
