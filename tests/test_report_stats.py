import json


def put(rows):
    from ark import history
    history.history_dir().mkdir(parents=True, exist_ok=True)
    (history.history_dir() / "jobs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def row(repo="arkimedes", tier="T1", outcome="merged", size="M", usd=1.0, minutes=20.0, budget_usd=2.0,
        budget_minutes=40.0, failure=None):
    return {"repo": repo, "plan": "p", "job": "j", "tier_start": tier, "outcome": outcome, "size": size,
            "usd": usd, "minutes": minutes, "budget_usd": budget_usd, "budget_minutes": budget_minutes,
            "failure": failure}


def test_insights_per_repo(ark_home):
    from ark import stats
    put([row(), row(usd=3.0, minutes=40.0), row(tier="T2", outcome="blocked", failure="job gate red"),
         row(repo="calc", size="S", usd=0.2, minutes=5.0, budget_usd=None)])
    text = stats.write().read_text()
    assert text.startswith("# Insights")
    assert "## arkimedes · 3 jobs" in text
    assert "merged by starting tier: T1 2/2 · T2 0/1" in text
    assert "median by size: M $1.00 · 20 min (n=3)" in text
    assert "actual / budget (median): USD 0.50 · time 0.50" in text
    assert "failures: job gate red ×1" in text
    assert "## calc · 1 jobs" in text


def test_insights_stay_within_30_lines(ark_home):
    from ark import stats
    put([row(repo=f"r{i}", failure=f"f{i}", outcome="blocked") for i in range(40)])
    lines = stats.write().read_text().splitlines()
    assert len(lines) <= 30 and lines[-1].startswith("…")


def test_no_history_says_so(ark_home):
    from ark import stats
    assert "No jobs in history yet." in stats.write().read_text()
