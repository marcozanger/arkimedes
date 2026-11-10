import json
import time

import pytest

from ark import beat, loop, paths


def test_no_beat_is_a_dead_loop(ark_home):
    assert beat.read() == {"alive": False, "last_beat": None, "pid": None}


def test_a_fresh_beat_is_alive_and_an_old_one_is_not(ark_home):
    assert beat.write(15) is None
    t = time.time()
    assert beat.read(t)["alive"] and beat.read(t + 59)["alive"]
    assert not beat.read(t + 61)["alive"]
    assert beat.read(t)["pid"] > 0 and beat.read(t)["last_beat"]


def test_a_slow_poll_widens_the_window(ark_home):
    beat.write(40)
    t = time.time()
    assert beat.read(t + 119)["alive"] and not beat.read(t + 121)["alive"]


def test_a_corrupt_beat_is_a_dead_loop(ark_home):
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    beat.file().write_text("{")
    assert not beat.read()["alive"]


def test_a_beat_that_cannot_be_written_says_why(ark_home, monkeypatch):
    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(beat.run, "_atomic", boom)
    assert "disk full" in beat.write(15)


def test_the_loop_beats_at_every_poll(ark_home, monkeypatch):
    class Stop(BaseException):
        pass
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "exclude": [], "tick_minutes": 60, "poll_seconds": 1}))
    seen = []

    def poll(_):
        seen.append(beat.read()["alive"])
        if len(seen) == 2:
            raise Stop
    monkeypatch.setattr(loop, "cycle", lambda: ("idle", [], False))
    monkeypatch.setattr(loop.time, "sleep", poll)
    with pytest.raises(Stop):
        loop._polling(False)
    assert seen == [True, True]
    assert json.loads(beat.file().read_text())["poll_seconds"] == 1
