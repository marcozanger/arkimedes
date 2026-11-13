import json

from ark import euklides, uidata


def _write(ark_home, name, lines):
    f = ark_home / "history" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lines) + "\n")


def test_history_newest_first_and_skips_broken_lines(ark_home):
    row = {"status": "done", "jobs": 2, "started": "s", "wall_minutes": 5, "budget_minutes": 9, "usd": 1.5, "budget_usd": 4}
    _write(ark_home, "plans.jsonl", [
        json.dumps({"repo": "r", "plan": "old", "ended": "2026-11-01T00:00:00+00:00", **row}),
        "{broken",
        json.dumps({"repo": "r", "plan": "new", "ended": "2026-11-03T00:00:00+00:00", **row})])
    got = uidata.history()
    assert [(h["repo"], h["slug"]) for h in got] == [("r", "new"), ("r", "old")]
    assert set(got[0]) == {"repo", "slug", "status", "jobs", "started", "ended", "wall_minutes",
                           "budget_minutes", "usd", "budget_usd"}
    assert got[0]["usd"] == 1.5


def test_history_empty(ark_home):
    assert uidata.history() == []


def test_euklides(ark_home):
    (ark_home / "history" / "euklides").mkdir(parents=True)
    with open(ark_home / "history" / "euklides" / "decisions.jsonl", "a") as f:
        for i in range(35):
            f.write(json.dumps({"id": f"r/s/j/{i}", "at": f"2026-11-01T00:{i:02d}:00+00:00", "action": "x"}) + "\n")
    euklides.add_review("r/s/j/34", "good", "fine")
    euklides.lessons_file().write_text("a lesson\n")
    e = uidata.euklides()
    assert len(e["decisions"]) == 30 and e["decisions"][0]["id"] == "r/s/j/34"
    assert e["decisions"][0]["review"] == {"verdict": "good", "comment": "fine"}
    assert e["decisions"][1]["review"] is None
    assert e["lessons"] == "a lesson" and e["review_command"] == "ark review"
    assert set(e) == {"model", "usd_per_call", "minutes_per_call", "tools", "max_per_job", "lessons", "decisions",
                      "review_command"}


def test_euklides_survives_failures(ark_home, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(euklides, "decisions", boom)
    monkeypatch.setattr(euklides, "lessons", boom)
    e = uidata.euklides()
    assert e["decisions"] == [] and e["lessons"] is None
