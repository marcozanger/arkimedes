import os
import subprocess
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def env_for(ark_home, **extra):
    env = {**os.environ, "ARK_HOME": str(ark_home)}
    env.pop("ARK_ROLE", None)
    env.update(extra)
    return env


def test_bin_ark_runs_the_cli(make_feature, ark_home):
    make_feature()
    r = subprocess.run([str(CODE / "bin/ark"), "status"], capture_output=True, text=True, env=env_for(ark_home))
    assert r.returncode == 0, r.stderr
    assert "notes-edit" in r.stdout


def test_bin_ark_validate(make_feature, ark_home):
    p = make_feature()
    r = subprocess.run([str(CODE / "bin/ark-validate"), str(p)], capture_output=True, text=True,
                       env=env_for(ark_home))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("OK")
