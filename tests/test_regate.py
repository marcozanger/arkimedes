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
