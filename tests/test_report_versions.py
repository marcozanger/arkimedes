import pytest

from report_helpers import mjobs, receipt, result_line, run_state

from ark import history, report, steps, summary, versions


def test_rows_are_bucketed_by_version_start():
    timeline = [{"version": 1, "approved_at": "2026-11-12T10:00:00+00:00"},
                {"version": 2, "approved_at": "2026-11-12T12:00:00+00:00"}]
    rows = [{"start": "2026-11-12T10:30:00+00:00", "end": "2026-11-12T11:00:00+00:00", "usd": 1.0, "minutes": 30},
            {"start": "2026-11-12T12:10:00+00:00", "end": "2026-11-12T12:20:00+00:00", "usd": 0.5, "minutes": 10},
            {"start": None, "end": None, "usd": None, "minutes": None}]
    out = versions.by_version(rows, timeline)
    assert [(v["version"], v["usd"], v["minutes"]) for v in out] == [(1, 1.0, 30), (2, 0.5, 10)]


@pytest.fixture
def report_state(ark_home):
    receipt("shell.worker", 0, 10, usd=1.25, log=result_line(usd=1.25))
    st = run_state()
    return st, steps.build(st), mjobs()


def _st_with_versions(st):
    st["versions"] = [{"version": 1, "approved_at": "2026-11-12T10:00:00+00:00", "changes": {}},
                      {"version": 2, "approved_at": "2026-11-12T12:00:00+00:00",
                       "changes": {"a": "unchanged", "b": "changed", "d": "added"}}]
    st["retired_jobs"] = {"b@v1": {"status": "merged", "usd": 0.4}}
    return st


def test_the_report_has_a_versions_section(report_state):
    st, rows, mjobs_ = report_state
    s = summary.summarize(_st_with_versions(st), rows, mjobs_)
    html = report.render_html(s, rows, None)
    assert "<h2>Versions</h2>" in html and "v1" in html and "v2" in html
    assert "changed: b" in html and "added: d" in html
    assert "b@v1" in html


def test_a_run_with_one_version_has_no_versions_section(report_state):
    st, rows, mjobs_ = report_state
    html = report.render_html(summary.summarize(st, rows, mjobs_), rows, None)
    assert "<h2>Versions</h2>" not in html


def test_history_keeps_the_version(report_state):
    st, rows, mjobs_ = report_state
    s = summary.summarize(_st_with_versions(st), rows, mjobs_)
    assert history.plan_row(s)["version"] == 2
