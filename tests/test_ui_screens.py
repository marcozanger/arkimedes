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
SPEC_MD = """---
status: {status}
---
# The draft screen

Why the page reviews drafts.

## Goal

Review the spec and the plan per section, from the page.

## Screens

- the draft screen
- the confirmation

```text
## not a section: inside a fence
```

## Errors

A changed plan is reloaded, not approved.
"""
PLAN_MD = """---
status: draft
repo: {repo}
slug: {slug}
created: 2026-11-13
---
# The draft screen

## Goal

The draft screen.

## Run manifest

```yaml
jobs:
  - id: page-draft
    budget: {{ minutes: 60, usd: 6 }}
  - id: page-home
    budget: {{ minutes: 40, usd: 3.5 }}
```
"""


def _review(doc, keys, verdicts, warning=None):
    secs = [{"key": k, "title": t, "hash": "h", "verdict": v, "note": "Say why." if v == "change" else "", "at": None}
            for (k, t), v in zip(keys, verdicts)]
    return {"doc": doc, "sha256": f"{doc}-sha", "sections": secs, "warning": warning}


SPEC_KEYS = [("goal", "Goal"), ("screens", "Screens"), ("errors", "Errors")]
PLAN_KEYS = [("goal", "Goal"), ("run-manifest", "Run manifest")]
WARNING = "review.json could not be read; it is rewritten on the next save"


def _detail(repo, slug, spec_status, spec_verdicts, plan_verdicts, problems=(), warning=None):
    estimate = None if problems else {"total_usd": 9.5, "total_minutes": 100, "end_to_end_minutes": 100}
    return {"repo": repo, "slug": slug, "title": "The draft screen", "approval": "-", "updated": 1762500000, "next": "",
            "spec": {"status": spec_status, "text": SPEC_MD.format(status=spec_status), "sha256": "spec-sha",
                     "review": _review("spec", SPEC_KEYS, spec_verdicts, warning)},
            "plan": {"status": "draft", "text": PLAN_MD.format(repo=repo, slug=slug), "sha256": "plan-sha",
                     "review": _review("plan", PLAN_KEYS, plan_verdicts), "problems": list(problems), "estimate": estimate}}


DETAILS = {
    "/api/draft?repo=arkimedes&slug=draft-screen": _detail("arkimedes", "draft-screen", "draft", ["ok", "change", None],
                                                           [None, None], warning=WARNING),
    "/api/draft?repo=socrates&slug=export": _detail("socrates", "export", "approved", ["ok", "ok", "ok"], ["ok", "ok"]),
    "/api/draft?repo=socrates&slug=broken": _detail("socrates", "broken", "approved", ["ok", "ok", "ok"], ["ok", "ok"],
                                                    problems=["jobs: page-home has no gate"]),
}
# what a POST answers, by path and by the page's query string; the default echoes like the real server
REPLIES = {
    "POST /api/approve-spec": {"status": 200, "body": {"status": "approved", "sha256": "spec-sha-2"}},
    "POST /api/approve-plan": {"status": 200, "body": {"approval": "ok"}},
    "POST /api/approve-plan?changed": {"status": 409, "body": {"error": "the plan changed since it was shown; look at it again.", "kind": "changed"}},
    "POST /api/approve-plan?invalid": {"status": 422, "body": {"error": "the plan does not meet the contract.", "kind": "invalid",
                                                               "problems": ["jobs: page-draft has no gate", "tasks: T9 is in no job"]}},
    "POST /api/approve-plan?forbidden": {"status": 403, "body": {"error": "forbidden token"}},
}
ROUTES = [("home", "#/"), ("plan", "#/plan/arkimedes/ui-polish/questions"), ("history", "#/history"), ("euklides", "#/euklides"),
          ("draft", "#/draft/arkimedes/draft-screen/spec"), ("dashboard", "#/dashboard")]
SIZES = [(1360, 900), (390, 844)]

STUB = """<script>
const ARK_SAMPLE = %s;
window.fetch = async (url, init) => {
  const path = String(url).split("?")[0], method = (init && init.method) || "GET";
  if (method === "POST") {
    window.ARK_POSTS = (window.ARK_POSTS || []).concat({ path, token: init.headers["X-Ark-Token"], body: JSON.parse(init.body) });
    const b = JSON.parse(init.body);
    const reply = ARK_SAMPLE["POST " + path + location.search] || ARK_SAMPLE["POST " + path];
    if (reply) return new Response(JSON.stringify(reply.body), { status: reply.status });
    if (path === "/api/review") {
      const d = ARK_SAMPLE["/api/draft?repo=" + b.repo + "&slug=" + b.slug], rv = JSON.parse(JSON.stringify(d[b.doc].review));
      rv.sections.forEach(s => { if (s.key === b.key) Object.assign(s, { verdict: b.verdict, note: b.verdict ? b.note : "" }); });
      d[b.doc].review = rv;
      return new Response(JSON.stringify({ ...rv, warning: null }), { status: 200 });
    }
    return new Response(JSON.stringify({ id: b.id, text: b.text }), { status: 200 });
  }
  window.ARK_GETS = (window.ARK_GETS || []).concat(String(url));
  const body = ARK_SAMPLE[String(url)] !== undefined ? ARK_SAMPLE[String(url)] : ARK_SAMPLE[path];
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


FLOWS = """<!doctype html><html><body>
<pre id="result"></pre>
<script>
const wait = ms => new Promise(r => setTimeout(r, ms));
async function until(f, what) {
  for (let i = 0; i < 300; i++) { const v = f(); if (v) return v; await wait(25); }
  throw new Error("timed out waiting for " + what);
}
function open(src) {
  const f = document.createElement("iframe");
  f.src = src; f.width = 1360; f.height = 900;
  document.body.appendChild(f);
  return until(() => f.contentDocument && f.contentDocument.body && f.contentDocument.body.dataset.rendered === "1"
    && f.contentDocument.querySelector("[data-draft-act]") && f, "the draft").then(() => f);
}
const main = f => f.contentDocument.querySelector("main").innerText;
const act = f => f.contentDocument.querySelector("[data-draft-act]");
const click = (f, sel) => until(() => f.contentDocument.querySelector(sel), sel).then(el => el.click());
const FLOWS = {
  // OK and Change save at once, a note on blur, and the header button follows the review
  async review() {
    const f = await open("/page.html#/draft/arkimedes/draft-screen/spec"), d = f.contentDocument, out = {};
    out.first = act(f).textContent;
    out.warning = main(f).includes("review.json could not be read");
    await click(f, '[data-verdict="ok"][data-key="errors"]');
    await until(() => (f.contentWindow.ARK_POSTS || []).length === 1, "the first review");
    const box = d.querySelector('textarea[data-note="screens"]');
    box.value = "Name the dialog.";
    box.dispatchEvent(new f.contentWindow.Event("input", { bubbles: true }));
    box.dispatchEvent(new f.contentWindow.Event("change", { bubbles: true }));
    await until(() => f.contentWindow.ARK_POSTS.length === 2, "the note");
    await wait(100);
    out.stillCopy = act(f).textContent;
    await click(f, '[data-verdict="ok"][data-key="screens"]');
    await until(() => f.contentWindow.ARK_POSTS.length === 3 && act(f).textContent === "Approve spec", "approve spec");
    out.enabled = !act(f).disabled;
    act(f).click();
    await until(() => f.contentWindow.ARK_POSTS.length === 4, "the approval");
    await wait(200);
    out.posts = f.contentWindow.ARK_POSTS;
    out.hash = f.contentWindow.location.hash;
    return out;
  },
  // Approve plan… opens a confirmation; Approve and queue sends the shown sha and goes Home
  async approve() {
    const f = await open("/page.html#/draft/socrates/export/plan"), out = {};
    out.button = act(f).textContent;
    out.numbers = f.contentDocument.querySelector(".numbers").innerText;
    act(f).click();
    await click(f, "[data-approve-plan]");
    out.confirm = f.contentDocument.querySelector("[data-confirm]").innerText;
    f.contentDocument.querySelector("[data-approve-plan]").click();
    await until(() => f.contentWindow.location.hash === "#/", "home");
    await wait(200);
    out.posts = f.contentWindow.ARK_POSTS;
    out.home = main(f);
    return out;
  },
  async changed() { return refused("changed"); },
  async invalid() { return refused("invalid"); },
  async forbidden() { return refused("forbidden"); },
  // a plan with problems: the button is disabled and the problems are listed
  async problems() {
    const f = await open("/page.html#/draft/socrates/broken/plan");
    return { button: act(f).textContent, disabled: act(f).disabled, text: main(f) };
  },
};
async function refused(q) {
  const f = await open("/page.html?" + q + "#/draft/socrates/export/plan");
  act(f).click();
  await click(f, "[data-approve-plan]");
  const gets = () => (f.contentWindow.ARK_GETS || []).filter(u => u.startsWith("/api/draft?")).length;
  const before = gets();
  f.contentDocument.querySelector("[data-approve-plan]").click();
  await until(() => f.contentDocument.querySelector(".draft-msg"), "the message");
  await wait(300);
  return { posts: f.contentWindow.ARK_POSTS, msg: f.contentDocument.querySelector(".draft-msg").innerText,
           reloaded: gets() - before, hash: f.contentWindow.location.hash, confirm: !!f.contentDocument.querySelector("[data-confirm]") };
}
(async () => {
  const out = {};
  for (const [name, flow] of Object.entries(FLOWS)) {
    try { out[name] = await flow(); } catch (e) { out[name] = { error: String(e) }; }
  }
  document.getElementById("result").textContent = JSON.stringify(out);
})();
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
            "/api/history": HISTORY, "/api/euklides": EUKLIDES, **DETAILS, **REPLIES}
    page = (UI / "index.html").read_text().replace("<head>", "<head>" + STUB % json.dumps(data).replace("</", "<\\/"), 1)
    (root / "page.html").write_text(page)
    for w, h in SIZES:
        frames = "".join(f'<iframe data-name="{name}" src="/page.html{route}" width="{w}" height="{h}" style="border:0;display:block"></iframe>'
                         for name, route in ROUTES)
        (root / f"outer-{w}.html").write_text(OUTER % frames)
    (root / "answer.html").write_text(ANSWER)
    (root / "flows.html").write_text(FLOWS)
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
    dash = out["dashboard"]["text"]
    for want in ("Last 30 days", "Reset filters", "Jobs merged", "Median plan time", "Euklides decisions", "Spend per day",
                 "Plans by status", "Spend by project", "API cleanup", "Mods API"):
        assert want.lower() in dash.lower(), want   # labels are upper-cased by CSS
    draft = out["draft"]["text"]
    for want in ("Copy resume command", "1 of 3 ok", "1 to change", "Goal", "Screens", "Errors", "OK", "Change",
                 "review.json could not be read", "## not a section: inside a fence"):
        assert want in draft, want


def test_an_open_question_is_answered_from_the_plan_screen(site, profile):
    chrome, base, _ = site
    out = _dump(chrome, f"{base}/answer.html", 390, False, profile)
    assert out["sent"] == [{"path": "/api/answer", "token": "{{ARK_TOKEN}}",
                            "body": {"repo": "arkimedes", "slug": "ui-polish", "id": "q2", "text": "Yes, as tooltips."}}]
    assert out["msg"].startswith("Sent: Yes, as tooltips.") and out["form"] is False


@pytest.fixture(scope="module")
def flows(site, tmp_path_factory):
    chrome, base, _ = site
    d = tempfile.mkdtemp(prefix="ark-ui-", dir="/tmp" if Path("/tmp").is_dir() else None)
    try:
        yield _dump(chrome, f"{base}/flows.html", 1360, False, d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _sent(out):
    return [(p["path"], p["body"]) for p in out["posts"]]


def test_the_draft_header_button_follows_the_review(flows):
    out = flows["review"]
    assert "error" not in out, out
    assert out["first"] == "Copy resume command" and out["warning"] is True
    assert out["stillCopy"] == "Copy resume command" and out["enabled"] is True
    who = {"repo": "arkimedes", "slug": "draft-screen"}
    assert _sent(out) == [
        ("/api/review", {**who, "doc": "spec", "key": "errors", "verdict": "ok", "note": ""}),
        ("/api/review", {**who, "doc": "spec", "key": "screens", "verdict": "change", "note": "Name the dialog."}),
        ("/api/review", {**who, "doc": "spec", "key": "screens", "verdict": "ok", "note": "Name the dialog."}),
        ("/api/approve-spec", {**who, "sha256": "spec-sha"}),
    ]
    assert all(p["token"] == "{{ARK_TOKEN}}" for p in out["posts"])
    assert out["hash"] == "#/draft/arkimedes/draft-screen/plan"


def test_approving_the_plan_sends_the_shown_sha_and_goes_home(flows):
    out = flows["approve"]
    assert "error" not in out, out
    assert out["button"] == "Approve plan…"
    assert "Approved" in out["numbers"] and "Reopen" in out["numbers"] and "$9.50" in out["numbers"]
    assert "2 jobs" in out["confirm"] and "$9.50" in out["confirm"] and "~1h40m" in out["confirm"]
    assert _sent(out) == [("/api/approve-plan", {"repo": "socrates", "slug": "export", "sha256": "plan-sha"})]
    assert "Approved socrates/export" in out["home"] and "Approved, not started" in out["home"]


def test_a_refused_approval_says_why(flows):
    changed, invalid, forbidden = flows["changed"], flows["invalid"], flows["forbidden"]
    for out in (changed, invalid, forbidden):
        assert "error" not in out, out
        assert out["hash"] == "#/draft/socrates/export/plan" and len(out["posts"]) == 1
    assert changed["msg"] == "The plan changed while you were reading it; look again" and changed["reloaded"] >= 1
    assert changed["confirm"] is False
    assert "jobs: page-draft has no gate" in invalid["msg"] and "tasks: T9 is in no job" in invalid["msg"]
    assert forbidden["msg"] == "Reload the page: ark ui restarted"


def test_a_plan_with_problems_cannot_be_approved(flows):
    out = flows["problems"]
    assert "error" not in out, out
    assert out["button"] == "Approve plan…" and out["disabled"] is True
    assert "jobs: page-home has no gate" in out["text"]


LIVE = """<!doctype html><html><body>
<pre id="result"></pre>
<script>
const wait = ms => new Promise(r => setTimeout(r, ms));
async function until(f, what) {
  for (let i = 0; i < 300; i++) { const v = f(); if (v) return v; await wait(25); }
  throw new Error("timed out waiting for " + what);
}
function open(src) {
  const f = document.createElement("iframe");
  f.src = src; f.width = 1360; f.height = 900;
  document.body.appendChild(f);
  return new Promise(ok => f.addEventListener("load", () => {
    f.contentWindow.ERRS = [];
    f.contentWindow.addEventListener("error", e => f.contentWindow.ERRS.push(String(e.message)));
    ok(f);
  }));
}
const ROUTES = ["#/", "#/plan/humanharness/notes-edit/timeline", "#/plan/arkimedes/ui-polish/questions",
  "#/plan/humanharness/notes-edit/events", "#/plan/humanharness/notes-edit/changes", "#/history", "#/euklides",
  "#/dashboard", "#/draft/socrates/export/plan"];
(async () => {
  const out = {};
  try {
    const f = await open("/page.html#/plan/humanharness/notes-edit/timeline"), d = f.contentDocument, w = f.contentWindow;
    await until(() => d.body.dataset.rendered === "1" && d.querySelector("canvas.graph"), "the graph");
    out.loopOn = d.body.dataset.loop;
    d.querySelector('[data-job="ui-edit"]').click();
    await until(() => d.querySelector(".jpanel"), "the panel");
    out.panel = d.querySelector(".jpanel").innerText;
    out.focus = d.activeElement && d.activeElement.id;
    d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await wait(50);
    out.closed = !d.querySelector(".jpanel");
    d.getElementById("motion").click();
    await wait(100);
    out.paused = d.body.classList.contains("paused");
    out.loopOff = d.body.dataset.loop;
    out.errors = {};
    for (const r of ROUTES) {
      w.location.hash = r;
      await until(() => d.body.dataset.rendered === "1", r);
      await wait(150);
      out.errors[r] = w.ERRS.slice();
      w.ERRS.length = 0;
    }
    w.location.hash = "#/";
    await wait(150);
    out.minis = d.querySelectorAll(".mini-box canvas").length;
    out.ticker = (d.querySelector(".ticker") || {}).innerText || "";
    w.location.hash = "#/draft/socrates/export/plan";
    await until(() => d.querySelector('canvas[data-src="draft"]'), "the draft graph");
    out.draftGraph = true;
  } catch (e) { out.error = String(e); }
  document.getElementById("result").textContent = JSON.stringify(out);
})();
</script></body></html>"""


def test_the_live_constellation_opens_the_job_panel_and_pauses(site, profile):
    chrome, base, root = site
    (root / "live.html").write_text(LIVE)
    out = _dump(chrome, f"{base}/live.html", 1360, False, profile)
    assert "error" not in out, out
    assert out["loopOn"] == "on"
    assert "ui-edit" in out["panel"] and "Time" in out["panel"] and "Spend" in out["panel"] and "Waits for" in out["panel"]
    assert out["focus"] == "jp-close" and out["closed"] is True
    assert out["paused"] is True and out["loopOff"] == "off"
    assert all(not e for e in out["errors"].values()), out["errors"]
    assert out["minis"] >= 3 and "running" in out["ticker"]
    assert out["draftGraph"] is True
