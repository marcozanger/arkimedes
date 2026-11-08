from ark.estimate import end_to_end, estimate


def j(id, minutes, after=(), usd=1, tier="T1"):
    return {"id": id, "tier": tier, "after": list(after), "budget": {"minutes": minutes, "usd": usd}}


def test_a_chain_is_the_sum():
    assert end_to_end([j("a", 10), j("b", 20, ["a"]), j("c", 5, ["b"])], writers=3) == 35


def test_independent_jobs_under_the_limit_take_the_longest():
    assert end_to_end([j("a", 10), j("b", 25), j("c", 5)], writers=3) == 25


def test_more_independent_jobs_than_writers_queue():
    # a and b start at 0; c starts when a ends at 10 and ends at 40; b ends at 20
    assert end_to_end([j("a", 10), j("b", 20), j("c", 30)], writers=2) == 40


def test_a_diamond_waits_for_both_branches():
    jobs = [j("a", 10), j("b", 30, ["a"]), j("c", 5, ["a"]), j("d", 10, ["b", "c"])]
    assert end_to_end(jobs, writers=3) == 50


def test_no_jobs_or_a_missing_budget_gives_none():
    assert end_to_end([], writers=3) is None
    assert end_to_end([{"id": "a", "after": [], "budget": {}}], writers=3) is None


def test_an_unknown_after_gives_none_instead_of_looping():
    assert end_to_end([j("a", 10, ["ghost"])], writers=3) is None


def test_estimate_totals_and_rows():
    e = estimate({"jobs": [j("a", 10, usd=2), j("b", 20, ["a"], usd=3, tier="T2")]}, writers=3)
    assert e["total_minutes"] == 30 and e["total_usd"] == 5 and e["end_to_end_minutes"] == 30
    assert e["writers"] == 3
    assert e["jobs"][1] == {"id": "b", "tier": "T2", "after": ["a"], "minutes": 20, "usd": 3}
