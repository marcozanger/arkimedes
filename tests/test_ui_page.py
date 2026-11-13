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
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts and all(s == '<script type="module">' for s in scripts), scripts
    imports = re.findall(r"""^\s*import\b[^;]*?from\s+["']([^"']+)["']""", html, re.M)
    assert imports and all(i.startswith("/vendor/spec-review/") for i in imports), imports
    assert html.count("fetch(") == 1 and "async function api(" in html


def test_every_post_sends_the_token_from_the_page():
    html = PAGE.read_text()
    assert '<meta name="ark-token" content="{{ARK_TOKEN}}">' in html
    m = re.search(r"async function api\(.*?\n\}", html, re.S)
    assert m and '"X-Ark-Token": TOKEN' in m.group(0)
    assert "const TOKEN = document.querySelector('meta[name=\"ark-token\"]').content" in html
    assert re.findall(r'api\("(/api/[a-z-]+)", \{ method: "POST"', html) == ["/api/answer"]
    assert html.count('method: "POST"') == 1


def test_the_page_exposes_its_entry_points():
    html = PAGE.read_text()
    assert "window.update = function" in html and "window.openPlan = function" in html
    assert 'document.body.dataset.rendered = "1"' in html and "document.body.dataset.screen =" in html
    assert "hashchange" in html and "data-link" not in html
    for route in ('"#/"', '"#/history"', '"#/euklides"', '"#/dashboard"', "#/plan/"):
        assert route in html, route
    assert 'const TABS = ["timeline", "questions", "events", "decisions", "spec", "plan"]' in html


def test_html_is_built_only_through_render():
    html = PAGE.read_text()
    assert html.count(".innerHTML =") == 3        # banner, menu, main: all in render()
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
    assert {"running", "done", "blocked", "waiting"} <= statuses
    assert any(p["error"] for p in d["plans"])
    assert any(j["status"] == "blocked" for p in d["plans"] for j in p["jobs"])
    assert any(j["status"] == "waiting_answer" for p in d["plans"] for j in p["jobs"])
    assert any(p["status"] == "waiting" and p["approval"] == "ok" and not p["events"] for p in d["plans"])
    assert any(p["questions"] and p["answers"] and p["repo"] == "arkimedes" for p in d["plans"])
    assert any(p["spec_file"].count("\n## ") >= 2 for p in d["plans"])
    assert any(a["repo"] for a in d["alerts"])
    assert any(p["cost"]["closed"] for p in d["plans"])
    assert any(p["cost"]["usd"] > p["cost"]["budget_usd"] for p in d["plans"])
    assert {"personal", "globallogic"} <= {p["account"] for p in d["plans"]}   # what the dashboard filters on
    assert any(p["cost"]["closed_at"] and p["cost"]["tokens"] for p in d["plans"])
    assert any(p["euklides"]["asked"] for p in d["plans"]) and "aborted" in statuses


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
