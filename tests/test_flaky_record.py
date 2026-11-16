from pathlib import Path

import pytest

from report_helpers import iso, mjobs, run_state
from test_loop import SLUG, bare

from ark import brief, coachqueue, loop, report, run, steps, summary

FLAKY = [{"job": "docs-check", "kind": "integrate", "head": "abc", "note": "watch.test.ts and\nclock.test.ts time out",
          "tail": "x", "at": "2026-11-13T10:00:00"}]


def test_the_queue_file_is_named_after_the_main_checkout(tmp_path):
    assert coachqueue.queue_file(tmp_path / "ws" / "humanharness") == \
        Path.home() / ".claude" / "coach" / "queue" / "humanharness.md"


def test_one_item_in_the_queue_format(tmp_path):
    repo = tmp_path / "ws" / "humanharness"
    f = coachqueue.queue_file(repo)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("- [ ] 2026-11-01 · older item (from: x)")  # no newline at the end
    coachqueue.add_flaky(repo, {"repo": "humanharness", "slug": "self-checking-docs", "flaky": FLAKY})
    lines = f.read_text().splitlines()
    assert lines[0] == "- [ ] 2026-11-01 · older item (from: x)"
    assert len(lines) == 2 and lines[1].startswith("- [ ] ")
    assert "docs-check" in lines[1] and "watch.test.ts and clock.test.ts" in lines[1]
    assert lines[1].endswith("(from: ark:humanharness/self-checking-docs)")


@pytest.fixture(autouse=True)
def own_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


def test_queue_flaky_writes_once(tmp_path):
    c = bare(tmp_path)
    c.repo = tmp_path / "ws" / "demo"
    c.st["flaky"] = FLAKY
    loop._queue_flaky(c)
    loop._queue_flaky(c)
    f = coachqueue.queue_file(c.repo)
    assert c.st["flaky_queued"] is True
    assert len(f.read_text().splitlines()) == 1


def test_queue_flaky_without_flaky_writes_nothing(tmp_path):
    c = bare(tmp_path)
    c.repo = tmp_path / "ws" / "demo"
    loop._queue_flaky(c)
    assert not coachqueue.queue_file(c.repo).exists()


def test_a_failed_queue_write_is_kept_and_raises_nothing(tmp_path):
    c = bare(tmp_path)
    c.repo = tmp_path / "ws" / "demo"
    c.st["flaky"] = FLAKY
    folder = coachqueue.queue_file(c.repo).parent
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.write_text("not a folder")
    loop._queue_flaky(c)
    assert c.st["queue_error"] and not c.st.get("flaky_queued")
    loop._queue_flaky(c)  # not retried


def _html(st):
    rows = steps.build(st)
    return report.render_html(summary.summarize(st, rows, mjobs()), rows, None)


def test_the_report_lists_the_flaky_gates(ark_home):
    html = _html(run_state(flaky=FLAKY))
    assert ">Flaky gates<" in html and "docs-check" in html and "clock.test.ts" in html
    assert "queue item is missing" not in html


def test_the_report_says_a_queue_item_is_missing(ark_home):
    html = _html(run_state(flaky=FLAKY, queue_error="OSError: no room"))
    assert "The coach queue item is missing: OSError: no room" in html


def test_the_report_has_no_flaky_section_without_flaky(ark_home):
    assert "Flaky gates" not in _html(run_state())


def _state():
    st = run.new_state({"repo": "demo", "slug": SLUG, "plan": "/nowhere", "sha256": "0" * 64, "approved_at": "2026-11-13T10"})
    st["jobs"]["a"] = {"status": "merged", "tier": "T1", "tier_path": ["T1"], "pr": {"url": "https://x/1"}, "reason": ""}
    return st


def test_final_pr_body_has_flaky_tests():
    st = _state()
    st["flaky"] = [{**FLAKY[0], "note": "ping @someone times out"}]
    body = brief.final_pr_body(st)
    assert "## Flaky tests" in body and "`docs-check`" in body and "integration gate" in body
    assert "@someone" not in body


def test_fallback_decisions_has_flaky_gates():
    st = _state()
    st["flaky"] = FLAKY
    text = brief.fallback_decisions(st, {})
    assert "## Flaky gates" in text and "- `docs-check`" in text


def test_decisions_prompt_asks_for_each_flaky_gate():
    st = _state()
    st["flaky"] = FLAKY
    p = brief.decisions_prompt(st, {})
    assert "clock.test.ts" in p and "flaky" in p


def test_no_flaky_no_sections():
    st = _state()
    assert "Flaky" not in brief.final_pr_body(st)
    assert "Flaky" not in brief.fallback_decisions(st, {})
    assert "clock.test.ts" not in brief.decisions_prompt(st, {})
