"""Headless Chrome renders every screen of ui/index.html from sample data, light and dark, at 1360 and 390 px.

The page is served by a throwaway HTTP server (module scripts do not load from file://) with `fetch` stubbed
to answer from ui/sample.json and the sample drafts, history and Euklides data below. One outer page holds
an iframe per screen at the wanted width (headless windows are at least 500 px wide), waits for each to
render, and writes what it measured into its own DOM, which `--dump-dom` prints."""
import html
import json
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
CHROMES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium",
           "google-chrome", "google-chrome-stable", "chromium", "chromium-browser")

DRAFTS = [
    {"repo": "arkimedes", "slug": "draft-screen", "title": "The draft screen", "approval": "-", "updated": 1762500000,
     "spec": {"status": "draft", "review": {"ok": 2, "change": 1, "total": 5}},
     "plan": {"status": "draft", "review": {"ok": 0, "change": 0, "total": 4}, "problems": [],
              "estimate": {"total_usd": 18.5, "total_minutes": 210, "end_to_end_minutes": 150}},
     "next": "review spec"},
    {"repo": "socrates", "slug": "export", "title": "Export to PDF", "approval": "stale", "updated": 1762400000,
     "spec": None, "plan": {"status": "draft", "review": {"ok": 3, "change": 0, "total": 3}, "problems": [], "estimate": None},
     "next": "approve plan"},
]
HISTORY = [
    {"repo": "socrates", "slug": "api-cleanup", "status": "done", "jobs": 2, "started": "2026-11-06T09:00:00+00:00",
     "ended": "2026-11-06T11:10:00+00:00", "wall_minutes": 130, "budget_minutes": 110, "usd": 7.4, "budget_usd": 9},
    {"repo": "cockpit", "slug": "board-sync", "status": "blocked", "jobs": 3, "started": "2026-11-05T14:00:00+00:00",
     "ended": "2026-11-05T15:30:00+00:00", "wall_minutes": 90, "budget_minutes": 120, "usd": 12.2, "budget_usd": 10},
    {"repo": "humanharness", "slug": "a-plan-with-a-really-long-slug-that-has-to-wrap-on-a-phone", "status": "done",
     "jobs": 6, "started": "2026-11-01T08:00:00+00:00", "ended": "2026-11-01T13:00:00+00:00", "wall_minutes": 300,
     "budget_minutes": 320, "usd": 21, "budget_usd": 25},
]
EUKLIDES = {
    "model": "opus", "usd_per_call": 3, "minutes_per_call": 20, "tools": "Read,Grep,Glob", "max_per_job": 4,
    "lessons": "- Snapshot diffs after a dependency bump: regenerate, do not ask.\n- A red integration gate after a merge: rebuild the base first.",
    "decisions": [
        {"id": "muscleapp/mobile/onboarding-copy/screens/1", "at": "2026-11-07T09:40:00+00:00", "repo": "muscleapp",
         "slug": "mobile/onboarding-copy", "job": "screens", "trigger": "gate red 3 times", "situation": "snapshot tests differ",
         "action": "ask", "note": "Should the snapshots be regenerated?", "undo": None, "why": "Only Marco knows if the new look is right.",
         "proposal": None, "valid": True, "usd": 1.2, "marco": None, "review": None},
        {"id": "humanharness/notes-edit/ui-edit/1", "at": "2026-11-07T09:10:00+00:00", "repo": "humanharness",
         "slug": "notes-edit", "job": "ui-edit", "trigger": "review asked for changes", "situation": "",
         "action": "retry", "note": "Second pass with the review's comments.", "undo": None, "why": "The fix is small.",
         "proposal": None, "valid": True, "usd": 0.8, "marco": None, "review": {"verdict": "ok", "comment": ""}},
        {"id": "socrates/api-cleanup/tests/1", "at": "2026-11-06T10:00:00+00:00", "repo": "socrates", "slug": "api-cleanup",
         "job": "tests", "trigger": "worker failed", "situation": "", "action": "widen", "note": "Let the job touch conftest.py.",
         "undo": None, "why": "The fixture lives there.", "proposal": None, "valid": True, "usd": 0.9, "marco": None,
         "review": {"verdict": "wrong", "comment": "It should have asked first."}},
    ],
    "review_command": "ark review",
}
ROUTES = [("home", "#/"), ("plan", "#/plan/arkimedes/ui-polish/questions"), ("history", "#/history"), ("euklides", "#/euklides")]
SIZES = [(1360, 900), (390, 844)]

STUB = """<script>
const ARK_SAMPLE = %s;
window.fetch = async (url, init) => {
  const path = String(url).split("?")[0], method = (init && init.method) || "GET";
  if (method === "POST") {
    window.ARK_POSTS = (window.ARK_POSTS || []).concat({ path, token: init.headers["X-Ark-Token"], body: JSON.parse(init.body) });
    const b = JSON.parse(init.body);
    return new Response(JSON.stringify({ id: b.id, text: b.text }), { status: 200 });
  }
  const body = ARK_SAMPLE[path];
  return new Response(JSON.stringify(body === undefined ? { error: "not found" } : body), { status: body === undefined ? 404 : 200 });
};
</script>"""

OUTER = """<!doctype html><html><body style="margin:0">%s
<pre id="result"></pre>
<script>
const frames = [...document.querySelectorAll("iframe")];
const out = {};
function measure(f) {
  const d = f.contentDocument, w = f.contentWindow.innerWidth, main = d.querySelector("main");
  const wide = main ? [...main.querySelectorAll("*")].filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && (r.right > w + 1 || r.left < -1);
  }).map(el => el.tagName.toLowerCase() + "." + el.className) : ["no main"];
  return { rendered: d.body.dataset.rendered || "", screen: d.body.dataset.screen || "", width: w,
           scroll: d.documentElement.scrollWidth, wide: wide.slice(0, 5), text: (main ? main.innerText : "").slice(0, 4000) };
}
let tries = 0;
function check() {
  tries++;
  const ready = frames.every(f => f.contentDocument && f.contentDocument.body && f.contentDocument.body.dataset.rendered === "1");
  if (!ready && tries < 500) { setTimeout(check, 50); return; }
  setTimeout(() => {
    frames.forEach(f => { out[f.dataset.name] = measure(f); });
    document.getElementById("result").textContent = JSON.stringify(out);
  }, 300);
}
window.addEventListener("load", check);
</script></body></html>"""

ANSWER = """<!doctype html><html><body>
<iframe src="/page.html#/plan/arkimedes/ui-polish/questions" width="390" height="844"></iframe>
<pre id="result"></pre>
<script>
const f = document.querySelector("iframe");
let tries = 0;
function step() {
  const d = f.contentDocument, box = d && d.querySelector("textarea[data-q]");
  if (!box && ++tries < 100) { setTimeout(step, 50); return; }
  box.value = "Yes, as tooltips.";
  box.dispatchEvent(new f.contentWindow.Event("input", { bubbles: true }));
  d.querySelector("[data-answer]").click();
  setTimeout(() => {
    const msg = d.querySelector(".msg");
    document.getElementById("result").textContent = JSON.stringify({
      sent: f.contentWindow.ARK_POSTS, msg: msg ? msg.textContent : "", form: !!d.querySelector("textarea[data-q]") });
  }, 500);
}
window.addEventListener("load", step);
</script></body></html>"""


def _chrome():
    for c in CHROMES:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    chrome = _chrome()
    if not chrome:
        pytest.skip("Google Chrome / Chromium is not found")
    root = tmp_path_factory.mktemp("site")
    shutil.copytree(UI / "vendor", root / "vendor")
    data = {"/api/runs": json.loads((UI / "sample.json").read_text()), "/api/drafts": DRAFTS,
            "/api/history": HISTORY, "/api/euklides": EUKLIDES}
    page = (UI / "index.html").read_text().replace("<head>", "<head>" + STUB % json.dumps(data).replace("</", "<\\/"), 1)
    (root / "page.html").write_text(page)
    for w, h in SIZES:
        frames = "".join(f'<iframe data-name="{name}" src="/page.html{route}" width="{w}" height="{h}" style="border:0;display:block"></iframe>'
                         for name, route in ROUTES)
        (root / f"outer-{w}.html").write_text(OUTER % frames)
    (root / "answer.html").write_text(ANSWER)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield chrome, f"http://127.0.0.1:{srv.server_address[1]}", root
    srv.shutdown()


class _Quiet(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

    def log_message(self, *args):
        pass


@pytest.fixture
def profile():
    # a short path: Chrome hangs when its profile socket path is longer than macOS allows
    d = tempfile.mkdtemp(prefix="ark-ui-", dir="/tmp" if Path("/tmp").is_dir() else None)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _dump(chrome, url, width, dark, profile):
    args = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}", f"--window-size={max(width, 500) + 40},{4 * 900}",
            "--virtual-time-budget=40000", "--dump-dom", url]
    if dark:
        args.insert(1, "--force-dark-mode")
        args.insert(1, "--blink-settings=preferredColorScheme=0")
    # the suite's sandbox HOME makes Chrome hang at start: give it the real one
    env = {**os.environ, "HOME": pwd.getpwuid(os.getuid()).pw_dir}
    r = subprocess.run(args, capture_output=True, text=True, timeout=90, env=env)
    m = re.search(r'<pre id="result">(.*?)</pre>', r.stdout, re.S)
    assert m and m.group(1), r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(html.unescape(m.group(1)))


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize("width", [w for w, _ in SIZES])
def test_every_screen_renders_and_fits(site, width, dark, profile):
    chrome, base, _ = site
    out = _dump(chrome, f"{base}/outer-{width}.html", width, dark, profile)
    for name, _ in ROUTES:
        got = out[name]
        assert got["rendered"] == "1", (name, got)
        assert got["screen"] == name, (name, got)
        assert got["width"] == width, (name, got)
        assert not got["wide"] and got["scroll"] <= width, (name, got)
    assert "UI polish" in out["home"]["text"] and "The draft screen" in out["home"]["text"]
    assert "Keep the old labels as tooltips?" in out["plan"]["text"] and "Answer" in out["plan"]["text"]
    assert "Is 720 px the right break point?" in out["plan"]["text"]
    assert "api-cleanup" in out["history"]["text"] and "$7.40 of $9" in out["history"]["text"]
    assert "ark review" in out["euklides"]["text"] and "It should have asked first." in out["euklides"]["text"]


def test_an_open_question_is_answered_from_the_plan_screen(site, profile):
    chrome, base, _ = site
    out = _dump(chrome, f"{base}/answer.html", 390, False, profile)
    assert out["sent"] == [{"path": "/api/answer", "token": "{{ARK_TOKEN}}",
                            "body": {"repo": "arkimedes", "slug": "ui-polish", "id": "q2", "text": "Yes, as tooltips."}}]
    assert out["msg"].startswith("Sent: Yes, as tooltips.") and out["form"] is False
