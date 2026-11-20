"""Amendments: what a run changed about its approved plan, and every place Marco reads them."""
import json
from types import SimpleNamespace

import pytest

from ark import amend, cli, review, run, uidata
from ark.plan import load_plan

MANIFEST = {"jobs": [{"id": "j1", "area": ["src/a.py"], "budget": {"usd": 2.0}, "gate": "pytest -q"},
                     {"id": "j2", "area": ["src/b.py"], "budget": {"usd": 1.0}, "gate": "pytest -q"}]}


def _state(**over):
    st = {"repo": "demo", "slug": "notes-edit", "jobs": {"j1": {}, "j2": {}}, "grants": {}}
    st.update(over)
    return st


def _saved(**over):
    """A state run.save accepts, for the demo/notes-edit run."""
    from report_helpers import run_state
    return run_state(repo="demo", slug="notes-edit", status="running", grants={}, **over)


def test_add_numbers_and_trims():
    st = _state()
    a = amend.add(st, "j1", "area", [], ["src/c.py"], why="  a\n companion  ", by="runner")
    b = amend.add(st, "j1", "budget", 2.0, 3.0, why="x" * 900, by="Euklides", decision="demo/notes-edit/j1/1")
    assert (a["id"], b["id"]) == ("A1", "A2") and a["why"] == "a companion" and len(b["why"]) == amend.WHY_CHARS
    assert amend.of(st, "j1", "budget") == [b] and amend.of(st, kind="area") == [a] and amend.of(st, "j2") == []


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        amend.add(_state(), "j1", "scope", None, None, why="", by="runner")


def test_effective_applies_grants_raises_and_gates():
    st = _state(jobs={"j1": {"usd_raised": 1.5}, "j2": {}}, grants={"j1": ["src/a.py", "src/c.py"]})
    amend.add(st, "j1", "gate", "the plan's gate", "pytest -q tests/test_a.py", why="flaky e2e", by="Marco")
    e = amend.effective(MANIFEST, st)
    assert e["j1"] == {"area": ["src/a.py", "src/c.py"], "budget": 3.5, "gate": "pytest -q tests/test_a.py"}
    assert e["j2"] == {"area": ["src/b.py"], "budget": 1.0, "gate": "pytest -q"}


def test_effective_text_and_markdown():
    st = _state(jobs={"j1": {"usd_raised": 1.0}, "j2": {}}, grants={"j1": ["src/c.py"]})
    assert amend.markdown(st).startswith("No amendments")
    amend.add(st, "j1", "area", ["src/a.py"], ["src/a.py", "src/c.py"], why="a test helper", by="Euklides",
              decision="demo/notes-edit/j1/1")
    amend.add(st, "j1", "budget", 2.0, 3.0, why="cause → change", by="Euklides", decision="demo/notes-edit/j1/2")
    amend.add(st, "integration", "base", "abc", "def", why="fix(base): lint", by="Euklides", undo="run")
    out = amend.effective_text(MANIFEST, st)
    assert "j1: \n  area  + src/c.py\n  budget  $2.00 → $3.00" in out and "j2: as approved" in out
    assert "Base fixes on the integration branch:" in out and "A3 · integration · base: abc → def" in out
    assert "- A2 · j1 · budget: $2.00 → $3.00 · Euklides (demo/notes-edit/j1/2) — cause → change" in out


def test_the_report_lists_amendments_and_raises(ark_home):
    from report_helpers import run_state
    from test_report_render import render
    st = run_state()
    assert "None: the run kept to its approved plan." in render(st)
    amend.add(st, "j1", "area", [], ["src/<x>.py"], why="companion", by="runner")
    next(iter(st["jobs"].values()))["raises"] = [{"usd": 1.0, "diagnosis": {"cause": "slow e2e", "change": "split it"},
                                                  "outcome": "merged"}]
    html = render(st)
    assert ">Amendments<" in html and "src/&lt;x&gt;.py" in html and "<x>" not in html
    assert "Euklides' raises" in html and "slow e2e" in html and "split it" in html and "merged" in html


def test_the_review_card_shows_diagnosis_and_amendments(ark_home):
    st = _state()
    a = amend.add(st, "j1", "budget", 2.0, 3.0, why="w", by="Euklides", decision="demo/notes-edit/j1/1")
    d = {"id": "demo/notes-edit/j1/1", "repo": "demo", "slug": "notes-edit", "job": "j1", "trigger": "failure",
         "action": "raise", "note": "n", "raise_usd": 1.0, "diagnosis": {"cause": "c", "change": "ch"}}
    text = review.card(d, "merged", [a])
    assert "diagnosis: c → ch" in text and "amended: A1 · j1 · budget: $2.00 → $3.00" in text


def test_review_of_a_run_prints_its_amendments_and_links_them(ark_home):
    st = _saved()
    amend.add(st, "j1", "budget", 2.0, 3.0, why="w", by="Euklides", decision="demo/notes-edit/j1/1")
    run.save(st)
    d = {"id": "demo/notes-edit/j1/1", "repo": "demo", "slug": "notes-edit", "job": "j1", "trigger": "failure",
         "action": "raise", "note": "n", "undo": None, "marco": None, "at": "2026-11-20T00:00:00+00:00"}
    folder = ark_home / "history" / "euklides"
    folder.mkdir(parents=True)
    (folder / "decisions.jsonl").write_text(json.dumps(d) + "\n")
    said = []
    args = SimpleNamespace(target="demo/notes-edit", all=False, no_distill=True)
    assert review.run_review(args, ask=lambda _: "q", say=said.append) == 0
    text = "\n".join(said)
    assert "Amendments to the approved plan:" in text and text.count("A1 · j1 · budget") == 2


def test_the_ui_attaches_amendments_to_their_decision(ark_home):
    st = _saved()
    amend.add(st, "j1", "area", [], ["src/c.py"], why="w", by="Euklides", decision="demo/notes-edit/j1/1")
    amend.add(st, "j1", "area", [], ["src/d.py"], why="companion", by="runner")
    run.save(st)
    folder = ark_home / "history" / "euklides"
    folder.mkdir(parents=True)
    rows = [{"id": "demo/notes-edit/j1/1", "repo": "demo", "slug": "notes-edit", "at": "1", "action": "widen"},
            {"id": "demo/notes-edit/j1/2", "repo": "demo", "slug": "notes-edit", "at": "2", "action": "ask"},
            {"id": "gone/run/j/1", "repo": "gone", "slug": "run", "at": "3", "action": "ask"}]
    (folder / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    by_id = {d["id"]: d["amendments"] for d in uidata.euklides()["decisions"]}
    assert [a["id"] for a in by_id["demo/notes-edit/j1/1"]] == ["A1"]
    assert by_id["demo/notes-edit/j1/2"] == [] and by_id["gone/run/j/1"] == []


def test_diff_effective(make_feature, capsys):
    p = make_feature()
    jid = load_plan(p).manifest["jobs"][0]["id"]
    from report_helpers import job
    st = _saved(plan=str(p), jobs={jid: job("running", usd_raised=0.5)})
    amend.add(st, jid, "budget", 1.0, 1.5, why="w", by="Marco")
    run.save(st)
    assert cli.main(["diff", "demo/notes-edit", "--effective"]) == 0
    out = capsys.readouterr().out
    assert "# Effective plan · demo/notes-edit" in out and "budget" in out and "A1 · " in out


def test_diff_effective_without_a_run(make_feature, capsys):
    make_feature()
    assert cli.main(["diff", "demo/notes-edit", "--effective"]) == 1
    assert "has no run" in capsys.readouterr().err
