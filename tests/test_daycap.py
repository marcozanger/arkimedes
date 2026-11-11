from ark import caps, daycap, paths


def test_ask_once_per_account_and_day(ark_home):
    d = daycap.load()
    q, new = daycap.ask(d, "globallogic", "text")
    assert new and q["id"] == "c1" and q["account"] == "globallogic"
    q2, new2 = daycap.ask(d, "globallogic", "other text")
    assert not new2 and q2 is q
    daycap.save(d)
    assert daycap.load()["questions"][0]["id"] == "c1"


def test_a_new_day_starts_clean(ark_home, monkeypatch):
    d = daycap.load()
    daycap.ask(d, "globallogic", "t")
    d["stopped"].append("acme")
    daycap.save(d)
    monkeypatch.setattr(caps, "today", lambda: "2999-01-01")
    d = daycap.load()
    assert d["day"] == "2999-01-01" and d["questions"] == [] and d["stopped"] == []


def test_answers(ark_home):
    d = daycap.load()
    q, _ = daycap.ask(d, "globallogic", "t")
    assert daycap.check_answer("raise 20") is None
    assert daycap.check_answer("stop") is None
    for bad in ("", "raise", "raise -1", "raise x", "more please", "ok"):
        assert daycap.check_answer(bad)
    daycap.apply(d, q["id"], "raise 20")
    assert caps.raised_today("globallogic") == 20 and d["questions"] == []
    assert d["answers"][0]["answer"] == "raise 20"
    q, _ = daycap.ask(d, "globallogic", "t")
    daycap.apply(d, q["id"], "stop")
    assert "globallogic" in d["stopped"]
    assert daycap.ask(d, "globallogic", "t") == (None, False)


def test_drop_when_no_longer_capped(ark_home):
    d = daycap.load()
    daycap.ask(d, "globallogic", "t")
    daycap.drop_except(d, set())
    assert d["questions"] == []


def test_an_unreadable_file_starts_clean(ark_home):
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    (paths.runs_dir() / "daycap.json").write_text("{not json")
    assert daycap.load()["questions"] == []
