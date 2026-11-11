import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_monitor_runs import make_run  # noqa: E402

from ark import monitor, paths  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGE, SAMPLE = ROOT / "ui" / "index.html", ROOT / "ui" / "sample.json"


def test_the_page_loads_nothing_from_outside():
    html = PAGE.read_text()
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    for banned in ("@import", "XMLHttpRequest", "WebSocket", "eval(", "new Function", "messageHandlers", "window.webkit"):
        assert banned not in html, banned
    assert html.count("fetch(") == 1 and 'fetch("/api/runs"' in html


def test_the_page_exposes_its_entry_points():
    html = PAGE.read_text()
    assert "window.update = function" in html and "window.openPlan = function" in html
    assert 'document.body.dataset.rendered = "1"' in html
    assert "hashchange" in html and "data-link" not in html


def test_html_is_built_only_through_render():
    html = PAGE.read_text()
    assert html.count(".innerHTML =") == 3        # banner, sidebar, main: all in render()
    assert "insertAdjacentHTML" not in html and "document.write" not in html


def test_localstorage_only_in_the_store_helper():
    html = PAGE.read_text()
    m = re.search(r"const store = \{.*?\n\};", html, re.S)
    assert m and "try" in m.group(0)
    assert html.replace(m.group(0), "").count("localStorage") == 0


def test_the_sample_covers_every_screen_state():
    d = json.loads(SAMPLE.read_text())
    assert d["version"] == 1 and d["loop"]["alive"] is True
    statuses = {p["status"] for p in d["plans"]}
    assert {"running", "done", "blocked"} <= statuses
    assert any(p["error"] for p in d["plans"])
    assert any(j["status"] == "blocked" for p in d["plans"] for j in p["jobs"])
    assert any(a["repo"] for a in d["alerts"])
    assert any(p["cost"]["closed"] for p in d["plans"])
    assert any(p["cost"]["usd"] > p["cost"]["budget_usd"] for p in d["plans"])


def test_the_sample_matches_the_real_snapshot(make_feature):
    make_run(make_feature)
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    (paths.runs_dir() / "alerts.jsonl").write_text(json.dumps(
        {"at": "2026-11-07T10:00:00+00:00", "text": "x", "critical": False, "repo": "demo", "slug": "notes-edit", "job": "shell"}) + "\n")
    real, sample = monitor.build(), json.loads(SAMPLE.read_text())
    [rp] = real["plans"]
    sp = next(p for p in sample["plans"] if p["status"] == "running")
    pairs = [("top", real, sample), ("loop", real["loop"], sample["loop"]), ("alert", real["alerts"][0], sample["alerts"][0]),
             ("plan", rp, sp), ("times", rp["times"], sp["times"]), ("cost", rp["cost"], sp["cost"]),
             ("job", rp["jobs"][0], sp["jobs"][0])]
    for name, r, s in pairs:
        assert set(s) == set(r), name


def test_notification_logic():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "ui_logic.mjs"), str(PAGE)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_logic_block_is_pure():
    html = PAGE.read_text()
    assert html.count("// ---- logic: start ----") == 1 and html.count("// ---- logic: end ----") == 1
    block = html.split("// ---- logic: start ----")[1].split("// ---- logic: end ----")[0]
    for banned in ("window", "document", "Notification", "localStorage"):
        assert banned not in block, banned
