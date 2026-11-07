from report_helpers import job, mjobs, receipt, result_line, run_state

DECISIONS = """# Decisions — notes-edit

Plan: /x/plan.md · approved 2026-11-07 · closed 2026-11-07

## shell — merged

- **D1** · Kept the store in **sqlite** <img src=x onerror=alert(1)>. · _worker (T1)_
  <details><summary>más</summary>

  Why: see https://example.test/pr/1 and <script>alert(2)</script>
  </details>
  </details>
"""


def render(st, decisions=DECISIONS):
    from ark import report, steps, summary
    rows = steps.build(st)
    return report.render_html(summary.summarize(st, rows, mjobs()), rows, decisions)


def test_the_report_has_every_section_in_spanish(ark_home):
    receipt("shell.worker", 0, 10, usd=1.25, log=result_line(usd=1.25))
    html = render(run_state())
    for heading in ("Resumen", "Decisiones", "Por PR", "Línea de tiempo", "Agentes", "Por rol", "Por modelo"):
        assert f">{heading}<" in html
    assert "$1.25" in html and "USD equivalente API" in html
    assert "notes-edit" in html and "https://example.test/pr/9" in html
    assert html.count("<details>") == html.count("</details>") == 1


def test_untrusted_text_is_escaped(ark_home):
    receipt("shell.worker", 0, 10, exit=1, reason="<b>x</b>", log="")
    html = render(run_state(jobs={"shell": job("blocked", reason="<script>alert(3)</script>")}))
    assert "<script>" not in html and "<img" not in html and "<b>x" not in html
    assert "&lt;script&gt;alert(3)" in html and "&lt;img src=x" in html
    assert "<strong>sqlite</strong>" in html
    assert '<a href="https://example.test/pr/1">' in html


def test_estimated_cells_say_so(ark_home):
    receipt("shell.worker", 0, 10, usd=None, log="")
    assert "estimado" in render(run_state())


def test_no_decisions_file_is_said(ark_home):
    assert "Sin archivo de decisiones" in render(run_state(), decisions=None)


def test_write_puts_it_in_the_run_folder(ark_home):
    from ark import paths, report, steps, summary
    st = run_state()
    rows = steps.build(st)
    path = report.write(st, summary.summarize(st, rows, mjobs()), rows)
    assert path == paths.run_dir("demo", "notes-edit") / "report.html"
    assert path.read_text().startswith("<!doctype html>")


def test_summary_md_is_a_short_english_table(ark_home):
    from ark import report, steps, summary
    receipt("shell.worker", 0, 10, usd=1.25, log=result_line(usd=1.25))
    st = run_state()
    rows = steps.build(st)
    md = report.summary_md(summary.summarize(st, rows, mjobs()), "/ark/runs/demo/notes-edit/report.html")
    assert md.startswith("## Report\n")
    assert "| shell | merged |" in md and "$1.25" in md and "API-equivalent" in md
    assert "`/ark/runs/demo/notes-edit/report.html`" in md and "@" not in md


def test_final_pr_body_appends_the_summary_and_still_starts_with_decisions():
    from ark import brief
    st = {"slug": "notes-edit", "approved_at": "2026-11-07T09:00:00+00:00",
          "jobs": {"shell": {"status": "merged", "pr": {"url": "u"}, "reason": ""}}}
    plain = brief.final_pr_body(st)
    body = brief.final_pr_body(st, "## Report\n\nx\n")
    assert body.startswith("**Decisions:**") and body.startswith(plain.rstrip("\n"))
    assert body.endswith("## Report\n\nx\n")
