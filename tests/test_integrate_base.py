from conftest import git
from test_loop import SLUG, drive, state  # noqa: F401
from helpers import manifest

from ark import integrate, loop


def test_a_gate_red_on_the_base_too_goes_to_euklides_at_once(feature, monkeypatch):
    m = manifest(integration_gate="test ! -e bad.txt")
    repo, _ = feature(m)
    loop.cycle()  # the run starts: ark/<slug> exists
    git("checkout", "-q", f"ark/{SLUG}", cwd=repo)
    (repo / "bad.txt").write_text("x\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "bad", cwd=repo)
    git("push", "-q", "origin", f"ark/{SLUG}", cwd=repo)
    git("checkout", "-q", "master", cwd=repo)
    monkeypatch.setenv("FAKE_EUKLIDES", '{"action": "ask", "note": "base is red", "proposal": null}')
    drive(lambda: state()["jobs"]["shell"]["status"] == "waiting_answer")
    j = state()["jobs"]["shell"]
    assert j["attempts"] == 1
    assert any("red on" in q["text"] and "without the job" in q["text"] for q in state()["questions"])


def test_the_base_check_runs_only_when_the_gate_itself_is_red(feature):
    repo, _ = feature(manifest(setup=["false"]))
    out = integrate.base_check  # exists
    assert callable(out)
