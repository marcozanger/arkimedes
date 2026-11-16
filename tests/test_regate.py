"""regate: a red gate on a head that passed before runs again alone on the machine."""
import json

from ark import loop
from test_loop import bare, quiet_machine, receipt  # noqa: F401


# -- T4: the job remembers its passed heads and its last red gate

def test_a_green_gate_remembers_its_head(tmp_path):
    c = bare(tmp_path, "gating", head="abc")
    loop.on_gate(c, "shell", receipt())
    assert c.st["jobs"]["shell"]["green_heads"] == ["abc"]


def test_a_clean_review_remembers_its_head(tmp_path):
    c = bare(tmp_path, "reviewing", head="abc")
    loop.on_review(c, "shell", receipt('{"verdict": "CLEAN", "findings": []}'))
    assert "abc" in c.st["jobs"]["shell"]["green_heads"]


def test_a_red_job_gate_tells_euklides_which_gate_and_head(tmp_path):
    c = bare(tmp_path, "gating", head="abc")
    loop.on_gate(c, "shell", receipt("FAIL timing.test.ts", exit=1))
    red = c.st["jobs"]["shell"]["euklides"]["red"]
    assert red["kind"] == "gate" and red["head"] == "abc" and "timing.test.ts" in red["tail"]


def test_an_integration_gate_red_on_the_base_tells_euklides_too(tmp_path):
    c = bare(tmp_path, "integrating", head="abc")
    out = {"ok": False, "base": "b1", "head": "abc", "reason": "red", "tail": "FAIL watch.test.ts", "base_red": True}
    loop.on_integrate(c, "shell", receipt(json.dumps(out)))
    assert c.st["jobs"]["shell"]["euklides"]["red"] == {"kind": "integrate", "head": "abc",
                                                        "tail": "FAIL watch.test.ts"}


def test_green_heads_keep_the_last_ten_without_repeats():
    j = {}
    for h in [str(i) for i in range(12)] + ["5"]:
        loop._passed(j, h)
    assert len(j["green_heads"]) == loop.GREEN_HEADS and j["green_heads"][-1] == "5" \
        and j["green_heads"].count("5") == 1


# -- T5: Euklides may choose regate

from ark import brief, euklides  # noqa: E402


def job_state(**over):
    return {"usd": 20.0, "tier": "T2", "euklides_count": 0, **over}


def test_regate_opens_on_a_red_head_that_passed_even_over_budget():
    assert "regate" in euklides.allowed("failure", job_state(), 10, regate=True)


def test_regate_is_closed_without_the_runners_say_so():
    assert "regate" not in euklides.allowed("failure", job_state(), 10)


def test_regate_is_closed_after_the_decision_limit():
    assert euklides.allowed("failure", job_state(euklides_count=99), 10, regate=True) == ["ask"]


def test_the_loop_opens_regate_only_once_per_head_and_gate():
    red = {"kind": "gate", "head": "abc", "tail": "x"}
    assert loop.regate_open({"green_heads": ["abc"]}, {"red": red})
    assert not loop.regate_open({"green_heads": ["zzz"]}, {"red": red})
    assert not loop.regate_open({"green_heads": ["abc"], "regated": ["gate:abc"]}, {"red": red})
    assert loop.regate_open({"green_heads": ["abc"], "regated": ["integrate:abc"]}, {"red": red})
    assert not loop.regate_open({"green_heads": ["abc"]}, {})


def test_euklides_reads_what_regate_does():
    assert "regate" in brief.EUKLIDES_ACTIONS and "alone" in brief.EUKLIDES_ACTIONS["regate"]
