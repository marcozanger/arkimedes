import json

import pytest

from ark import paths, reserved


def diff(name, removed=(), added=(), deleted=False):
    head = [f"diff --git a/{name} b/{name}"]
    if deleted:
        head.append("deleted file mode 100644")
    head += [f"--- a/{name}", f"+++ b/{name}", "@@ -1 +1 @@"]
    return "\n".join(head + [f"-{x}" for x in removed] + [f"+{x}" for x in added]) + "\n"


# ---- R2: weakening verification ------------------------------------------------------------------

@pytest.mark.parametrize("d", [
    diff("tests/test_a.py", removed=["def test_one():", "    assert x == 1"]),
    diff("tests/test_a.py", removed=["    assert x == 1"]),
    diff("tests/test_a.py", added=["@pytest.mark.skip(reason='flaky')"]),
    diff("tests/test_a.py", added=["@pytest.mark.xfail"]),
    diff("lib/x.py", added=["    pytest.skip('later')"]),
    diff("src/a.test.ts", added=["it.skip('renders', () => {"]),
    diff("src/a.test.ts", removed=["  expect(x).toBe(1)"]),
    diff("tests/test_a.py", deleted=True, removed=["def test_one():"]),
])
def test_a_weaker_check_hits_r2(d):
    hits = reserved.weakened(d)
    assert hits and all(h["rule"] == "R2" for h in hits)


@pytest.mark.parametrize("d", [
    diff("tests/test_a.py", removed=["    assert x == 1"], added=["    assert x == 1, 'with a message'"]),
    diff("tests/test_a.py", added=["def test_two():", "    assert y"]),
    diff("tests/test_a.py", removed=["def test_one():"], added=["def test_one_renamed():"]),
    diff("tests/conftest.py", removed=["    time.sleep(0.1)"], added=["    wait_until(ready)"]),  # a flaky fix
    diff("tests/test_a.py", removed=["@pytest.mark.skip"]),  # a skip removed makes it stricter
    diff("lib/x.py", removed=["    assert invariant"]),  # an assert in code, not a test
    diff("docs/runner-control.md", removed=["assert this"]),
])
def test_a_check_that_stays_as_strong_does_not_hit(d):
    assert reserved.weakened(d) == []


# ---- R3 and R5 ---------------------------------------------------------------------------------

def test_a_file_in_another_live_jobs_area_hits_r3():
    hits = reserved.foreign(["src/ui/a.ts", "src/shell/b.ts"], {"ui": ["src/ui/**"], "other/run": ["src/ui/a.ts"]})
    assert hits == [{"rule": "R3", "file": "src/ui/a.ts", "why": "in the area of other/run, ui"}]


@pytest.mark.parametrize("f", ["package-lock.json", "web/package.json", "requirements.txt", "db/migrations/001.sql",
                               "pyproject.toml", "go.sum"])
def test_contracts_by_path_hit_r5(f):
    assert [h["rule"] for h in reserved.contracts([f], reserved.DEFAULT_GLOBS)] == ["R5"]


def test_a_plain_source_file_is_no_contract():
    assert reserved.contracts(["lib/ark/loop.py", "tests/test_x.py"], reserved.DEFAULT_GLOBS) == []


def test_what_cannot_be_read_is_a_hit():
    hits = reserved.check(["x.py"], None, None, [])
    assert {h["rule"] for h in hits} == {"R2", "R3"}


def test_a_clear_change_has_no_hits():
    assert reserved.check(["lib/x.py"], diff("lib/x.py", added=["x = 1"]), {"ui": ["src/ui/**"]},
                          reserved.DEFAULT_GLOBS) == []


def test_text_is_one_line_per_hit():
    assert reserved.text([{"rule": "R3", "file": "a", "why": "b"}, {"rule": "R2", "file": "", "why": "c"}]) == \
        "R3: a — b\nR2: c"


# ---- the other runs of the repo ------------------------------------------------------------------

def _run(ark_home, slug, jobs, status="running", area=("src/x/**",)):
    plan = ark_home / "desk" / "demo" / slug / "plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("plan")
    d = paths.run_dir("demo", slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"status": status, "plan": str(plan), "jobs": jobs,
                                              "grants": {"a": ["docs/a.md"]}}))
    return plan


def test_other_runs_give_their_live_jobs_areas(ark_home, monkeypatch):
    class P:
        manifest = {"jobs": [{"id": "a", "area": ["src/a/**"]}, {"id": "b", "area": ["src/b/**"]}]}
    monkeypatch.setattr("ark.plan.load_plan", lambda path: P)
    _run(ark_home, "mine", {"a": {"status": "working"}})
    _run(ark_home, "other", {"a": {"status": "working"}, "b": {"status": "merged"}})
    _run(ark_home, "old", {"a": {"status": "working"}}, status="done")
    assert reserved.other_runs_areas("demo", "mine") == {"other/a": ["src/a/**", "docs/a.md"]}


def test_an_unreadable_other_run_is_unknown(ark_home):
    d = paths.run_dir("demo", "broken")
    d.mkdir(parents=True)
    (d / "state.json").write_text("{")
    assert reserved.other_runs_areas("demo", "mine") is None


def test_no_runs_folder_means_no_other_areas(ark_home):
    assert reserved.other_runs_areas("nothing", "mine") == {}
