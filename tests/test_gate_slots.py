import json

import pytest
from helpers import job, manifest
from test_loop import drive, feature, limits, state  # noqa: F401  (feature is a fixture)

from ark import caps, loop, paths
from ark.validate import validate

LIMITS = {"writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1, "gates_total": 2}


def test_gates_total_defaults_to_two(ark_home):
    assert paths.load_workspace()["limits"]["gates_total"] == 2


@pytest.mark.parametrize("bad", [0, -1, 1.5, "2", True, None])
def test_a_gates_total_below_one_or_not_an_integer_is_rejected(bad):
    with pytest.raises(ValueError, match="limits.gates_total"):
        caps.check_limits({"writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1, "gates_total": bad})


def test_ark_validate_reports_a_bad_gates_total(ark_home, make_feature):
    p = make_feature()
    f = ark_home / "workspace.json"
    f.write_text(json.dumps({**json.loads(f.read_text()), "limits": {"gates_total": 0}}))
    assert any("gates_total" in x.message for x in validate(p))


def st_with(*jobs, fixes=()):
    """A run state with jobs given as (status, heavy) and base fixes given by status."""
    return {"repo": "a", "jobs": {f"j{i}": {"status": s, "heavy": h} for i, (s, h) in enumerate(jobs)},
            "fixes": [{"n": i, "status": s} for i, s in enumerate(fixes)]}


def test_a_third_gate_of_any_kind_waits():
    slots = loop.Slots([st_with(("gating", False), ("integrating", False))], {**LIMITS, "heavy_gates": 2})
    assert not slots.gate(False)


def test_two_light_gates_fit_and_a_third_waits():
    slots = loop.Slots([st_with()], LIMITS)
    assert slots.gate(False) and slots.gate(False) and not slots.gate(False)


def test_an_integration_gate_of_a_light_job_holds_the_heavy_slot():
    slots = loop.Slots([st_with(("integrating", False))], LIMITS)
    assert not slots.gate(True)   # a heavy gate waits
    assert slots.gate(False)      # a light one still fits gates_total


def test_a_base_fix_gate_counts_as_a_heavy_gate():
    slots = loop.Slots([st_with(fixes=("gating",))], LIMITS)
    assert not slots.gate(True) and slots.gate(False) and not slots.gate(False)


def test_gates_of_different_runs_share_the_slots():
    slots = loop.Slots([st_with(("gating", False)), {**st_with(("gating", False)), "repo": "b"}], LIMITS)
    assert not slots.gate(False)


def test_with_one_gate_slot_the_second_gate_waits(feature, ark_home, tmp_path):
    limits(ark_home, writers_per_repo=3, writers_total=6, heavy_gates=1, gates_total=1)
    flag = tmp_path / "release"
    wait = f'while [ ! -f "{flag}" ]; do sleep 0.05; done'
    feature(manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"], gate=wait),
                           job("ui", ["T3"], ["src/ui/**"], gate=wait)]))

    def one_gating():
        return sorted(j["status"] for j in state()["jobs"].values()) == ["gating", "needs_gate"]
    try:
        drive(one_gating)
    finally:
        flag.write_text("go")
