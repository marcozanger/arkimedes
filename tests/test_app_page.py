import json
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
PAGE, SAMPLE = APP / "index.html", APP / "sample.json"


def test_the_page_loads_nothing_from_outside():
    html = PAGE.read_text()
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    for banned in ("@import", "fetch(", "XMLHttpRequest", "WebSocket", "eval(", "new Function", "localStorage"):
        assert banned not in html, banned


def test_the_page_exposes_what_the_shell_calls():
    html = PAGE.read_text()
    assert "window.update = function" in html and "window.openPlan = function" in html
    assert 'document.body.dataset.rendered = "1"' in html
    assert "messageHandlers.ark" in html


def test_html_is_built_only_through_render():
    html = PAGE.read_text()
    assert html.count(".innerHTML =") == 3        # banner, sidebar, main: all in render()
    assert "insertAdjacentHTML" not in html and "document.write" not in html


def test_the_sample_covers_every_screen_state():
    d = json.loads(SAMPLE.read_text())
    assert d["version"] == 1 and d["loop"]["alive"] is True
    statuses = {p["status"] for p in d["plans"]}
    assert {"running", "done"} <= statuses
    assert any(p["error"] for p in d["plans"])
    assert any(j["status"] == "blocked" for p in d["plans"] for j in p["jobs"])
    assert any(a["repo"] for a in d["alerts"])
