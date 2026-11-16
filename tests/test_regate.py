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


# -- T6: the quiet re-run in the loop

LIMITS = {"writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1, "gates_total": 2}


def test_regate_holds_new_gates_and_new_workers_and_waits_for_every_gate():
    busy = {"repo": "a", "jobs": {"x": {"status": "gating", "heavy": False},
                                  "y": {"status": "needs_gate", "heavy": False, "regate": {"kind": "gate"}}}}
    slots = loop.Slots([busy], LIMITS)
    assert not slots.gate(False) and not slots.writer("a") and not slots.regate()
    idle = {"repo": "a", "jobs": {"y": {"status": "needs_gate", "heavy": False, "regate": {"kind": "gate"}}}}
    slots = loop.Slots([idle], LIMITS)
    assert slots.regate() and not slots.regate() and not slots.gate(False)


def test_slots_with_no_jobs_are_not_quiet():
    assert loop.Slots([], LIMITS).gate(False)


def test_a_regate_decision_sends_the_job_back_to_its_gate(tmp_path, monkeypatch):
    red = {"kind": "gate", "head": "abc", "tail": "FAIL a.test.ts"}
    c = bare(tmp_path, "euklides", head="abc", green_heads=["abc"],
             euklides={"trigger": "failure", "situation": "red", "kind": None, "red": red,
                       "allowed": ["regate", "ask"]})
    monkeypatch.setattr(loop.euklides, "record", lambda *a, **k: "demo/notes-edit/shell/1")
    loop.on_euklides(c, "shell", receipt('{"action": "regate", "note": "a.test.ts times out under load"}'))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_gate" and j["regate"]["head"] == "abc" and j["regated"] == ["gate:abc"]
    assert j["euklides_count"] == 1


def test_a_green_regate_goes_on_and_is_recorded_flaky(tmp_path):
    rg = {"kind": "gate", "head": "abc", "first_tail": "FAIL a", "note": "a.test.ts", "ctx": {}, "decision": None,
          "until": 9e9}
    c = bare(tmp_path, "gating", head="abc", regate=rg)
    loop.on_gate(c, "shell", receipt())
    j = c.st["jobs"]["shell"]
    assert j["status"] == "needs_pr" and "regate" not in j
    assert c.st["flaky"][0]["job"] == "shell" and c.st["flaky"][0]["note"] == "a.test.ts"


def test_a_red_regate_asks_marco_with_both_outputs(tmp_path):
    rg = {"kind": "gate", "head": "abc", "first_tail": "FIRST RED", "note": "a.test.ts",
          "ctx": {"trigger": "failure", "situation": "red", "kind": None}, "decision": None, "until": 9e9}
    c = bare(tmp_path, "gating", head="abc", regate=rg)
    loop.on_gate(c, "shell", receipt("SECOND RED", exit=1))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and "regate" not in j
    text = c.st["questions"][-1]["text"]
    assert "FIRST RED" in text and "SECOND RED" in text


def test_a_red_integration_regate_asks_marco_with_both_outputs(tmp_path):
    rg = {"kind": "integrate", "head": "abc", "first_tail": "FIRST RED", "note": "w.test.ts",
          "ctx": {"trigger": "blocked", "situation": "red", "kind": "integrate"}, "decision": None, "until": 9e9}
    c = bare(tmp_path, "integrating", head="abc", regate=rg)
    out = {"ok": False, "base": "b1", "head": "abc", "reason": "red", "tail": "SECOND RED", "base_red": True}
    loop.on_integrate(c, "shell", receipt(json.dumps(out)))
    j = c.st["jobs"]["shell"]
    assert j["status"] == "waiting_answer" and "regate" not in j
    text = c.st["questions"][-1]["text"]
    assert "FIRST RED" in text and "SECOND RED" in text


def test_a_regate_past_its_deadline_asks_marco(tmp_path):
    rg = {"kind": "gate", "head": "abc", "first_tail": "FIRST RED", "note": "n",
          "ctx": {"trigger": "failure", "situation": "red", "kind": None}, "decision": None, "until": 0}
    c = bare(tmp_path, "needs_gate", head="abc", regate=rg)
    slots = loop.Slots([c.st], LIMITS)
    loop._next_step(c, "shell", c.st["jobs"]["shell"], slots)()
    assert c.st["jobs"]["shell"]["status"] == "waiting_answer"
    assert "deadline" in c.st["questions"][-1]["text"]


def test_a_regate_on_a_moved_head_asks_marco(tmp_path):
    rg = {"kind": "gate", "head": "abc", "first_tail": "FIRST RED", "note": "n",
          "ctx": {"trigger": "failure", "situation": "red", "kind": None}, "decision": None, "until": 9e9}
    c = bare(tmp_path, "needs_gate", head="def", regate=rg)
    slots = loop.Slots([c.st], LIMITS)
    loop._next_step(c, "shell", c.st["jobs"]["shell"], slots)()
    assert c.st["jobs"]["shell"]["status"] == "waiting_answer" and "regate" not in c.st["jobs"]["shell"]
    assert "could not start" in c.st["questions"][-1]["text"]


def test_a_job_sent_to_euklides_or_blocked_drops_its_regate(tmp_path):
    c = bare(tmp_path, "gating", head="abc", regate={"kind": "gate"})
    loop._to_euklides(c.st, "shell", "blocked", "x")
    assert "regate" not in c.st["jobs"]["shell"]
    c.st["jobs"]["shell"]["regate"] = {"kind": "gate"}
    loop._block(c.st, "shell", "x")
    assert "regate" not in c.st["jobs"]["shell"]


# -- T7: a replay of the two cases, end to end

from helpers import manifest  # noqa: E402
from test_loop import drive, feature, jobs_settled, state  # noqa: E402,F401


def test_an_integration_gate_red_under_load_goes_on_without_a_question(feature, monkeypatch, tmp_path):
    busy = tmp_path / "busy"
    busy.write_text("load")
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "regate", "note": "watch.test.ts times out under load"}')
    monkeypatch.setenv("FAKE_EUKLIDES_RUN", f'rm -f "{busy}"')
    feature(manifest(integration_gate=f'[ ! -f "{busy}" ]'))
    drive(jobs_settled)
    st = state()
    assert all(j["status"] == "merged" for j in st["jobs"].values())
    assert not st.get("questions")
    assert [f["kind"] for f in st["flaky"]] == ["integrate"]


def test_a_job_gate_red_under_load_on_a_head_that_passed_goes_on_without_a_question(feature, monkeypatch, tmp_path):
    """The integration gate is red once (and loads the machine: `busy` appears), so the job goes back to its
    worker; the attempt changes nothing, so its job gate runs on the head that passed, and is red."""
    from helpers import job
    busy, once = tmp_path / "busy", tmp_path / "once"
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "regate", "note": "timing.test.ts is slow under load"}')
    monkeypatch.setenv("FAKE_EUKLIDES_RUN", f'rm -f "{busy}"')
    monkeypatch.setenv("FAKE_EMPTY", "shell:2")
    integration = f'if [ ! -f "{once}" ]; then touch "{once}" "{busy}"; exit 1; fi'
    feature(manifest(integration_gate=integration, jobs=[
        job("shell", ["T1", "T2"], ["src/shell/**"], gate=f'[ ! -f "{busy}" ]'),
        job("ui", ["T3"], ["src/ui/**"], after=["shell"])]))
    drive(jobs_settled)
    st = state()
    assert all(j["status"] == "merged" for j in st["jobs"].values())
    assert not st.get("questions")
    assert [(f["job"], f["kind"]) for f in st["flaky"]] == [("shell", "gate")]
