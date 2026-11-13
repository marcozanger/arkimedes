import json

import pytest
from test_ui_server import get
from test_ui_server import server  # noqa: F401  (the fixture)
from test_ui_writes import post

from ark import distill, euklides, paths, review, runreview

DECISIONS = """# Decisions — s

Plan: x · approved 2026-11-01 · closed 2026-11-02

## core — merged

- **D1** · Used a lock file instead of a pid check · _worker (sonnet)_
  <details><summary>more</summary>

  Why: pid files go stale
  Rejected: flock
  </details>
- **D2** · Skipped the retry · _reviewer_

## ui — blocked

- **D3** · Blocked on missing token · _arkimedes_
"""


@pytest.fixture
def run_file(ark_home):
    d = paths.run_dir("r", "s")
    d.mkdir(parents=True)
    (d / "decisions.md").write_text(DECISIONS)
    return d / "decisions.md"


def test_parse_finds_each_decision_with_its_job_and_details(run_file):
    items = runreview.parse(DECISIONS)
    assert [(i["id"], i["job"]) for i in items] == [("D1", "core — merged"), ("D2", "core — merged"), ("D3", "ui — blocked")]
    assert "Why: pid files go stale" in items[0]["text"] and "<details>" not in items[0]["text"]
    assert items[1]["text"] == "· Skipped the retry · _reviewer_"


def test_save_writes_a_review_row_with_the_text_and_load_shows_it(run_file):
    runreview.save("r", "s", "D1", "ok")
    out = runreview.save("r", "s", "D2", "change", " should retry ")
    rows = euklides.reviews()
    assert [(r["id"], r["verdict"], r["comment"]) for r in rows] == [("r/s/D1", "ok", ""), ("r/s/D2", "wrong", "should retry")]
    assert rows[0]["text"].startswith("· Used a lock file")
    assert [(i["id"], i["verdict"], i["comment"]) for i in out["items"]] == [("D1", "ok", ""), ("D2", "change", "should retry"), ("D3", None, "")]
    assert [i["id"] for i in runreview.pending("r", "s")] == ["D3"]


def test_the_last_verdict_wins_and_an_edited_decision_resets(run_file):
    runreview.save("r", "s", "D1", "change", "no")
    runreview.save("r", "s", "D1", "ok")
    assert runreview.load("r", "s")["items"][0]["verdict"] == "ok"
    run_file.write_text(DECISIONS.replace("lock file", "mutex"))
    assert runreview.load("r", "s")["items"][0]["verdict"] is None


def test_refusals(run_file):
    with pytest.raises(runreview.RunReviewError):
        runreview.save("r", "s", "D1", "change", "  ")
    with pytest.raises(runreview.RunReviewError):
        runreview.save("r", "s", "D1", "maybe")
    with pytest.raises(runreview.RunReviewError) as e:
        runreview.save("r", "s", "D9", "ok")
    assert e.value.kind == "unknown"
    assert euklides.reviews() == []


def test_followup(run_file):
    out = runreview.save("r", "s", "followup", "fix", "rerun the gate")
    assert out["followup"] == {"kind": "fix", "comment": "rerun the gate"}
    with pytest.raises(runreview.RunReviewError):
        runreview.save("r", "s", "followup", "ok")


def test_the_distill_sees_run_verdicts(run_file):
    runreview.save("r", "s", "D1", "change", "a pid check was enough")
    runreview.save("r", "s", "followup", "more", "docs missing")
    p = distill.prompt()
    assert "r/s/D1" in p and "a pid check was enough" in p and "Used a lock file" in p
    assert "r/s/followup" in p and "docs missing" in p


def test_ark_review_with_a_target_walks_the_run_decisions(run_file):
    out, answers = [], iter(["ok", "mal: wrong call", "q"])
    args = review.parser().parse_args(["r/s", "--no-distill"])
    review.run_review(args, ask=lambda _: next(answers), say=out.append)
    assert [(r["id"], r["verdict"]) for r in euklides.reviews()] == [("r/s/D1", "ok"), ("r/s/D2", "wrong")]
    assert "from decisions.md" in "\n".join(out)
    # without a target the run's file is not offered
    out.clear()
    review.run_review(review.parser().parse_args(["--no-distill"]), ask=lambda _: "q", say=out.append)
    assert out == ["Nothing to review."]


def test_http_get_and_post(server, run_file):  # noqa: F811
    st, _, body = get(server, "/api/decision-review?repo=r&slug=s")
    assert st == 200 and [i["id"] for i in json.loads(body)["items"]] == ["D1", "D2", "D3"]
    assert get(server, "/api/decision-review?repo=..&slug=s")[0] == 400
    st, got = post(server, "/api/decision-review", {"repo": "r", "slug": "s", "key": "D3", "verdict": "ok"})
    assert st == 200 and got["items"][2]["verdict"] == "ok"
    assert post(server, "/api/decision-review", {"repo": "r", "slug": "s", "key": "D3", "verdict": "change"})[0] == 400
    assert post(server, "/api/decision-review", {"repo": "r", "slug": "s", "key": "D8", "verdict": "ok"})[0] == 404


def test_a_runner_process_cannot_post_a_verdict(server, run_file, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ARK_ROLE", "worker")
    st, got = post(server, "/api/decision-review", {"repo": "r", "slug": "s", "key": "D1", "verdict": "ok"})
    assert st == 403 and euklides.reviews() == []
