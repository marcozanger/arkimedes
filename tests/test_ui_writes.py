import hashlib
import http.client
import json

import pytest
from helpers import plan_text
from test_ui_server import server  # noqa: F401  (the fixture)

from ark import questions, reviews, run

SPEC = "---\nstatus: draft\nrepo: r\nslug: s\ncreated: 2026-11-05\n---\n# S\n\n## Problem\n\nx\n\n## Goal\n\ny\n"
PATHS = ("/api/review", "/api/approve-spec", "/api/approve-plan", "/api/answer", "/api/decision-review")


def post(srv, path, body, token="ok", origin="ok", host=None, raw=None):
    port = srv.server_address[1]
    data = raw if raw is not None else json.dumps(body).encode()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("POST", path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{port}")
    if origin == "ok":
        origin = f"http://127.0.0.1:{port}"
    if origin:
        c.putheader("Origin", origin)
    if token == "ok":
        token = srv.token
    if token is not None:
        c.putheader("X-Ark-Token", token)
    c.putheader("Content-Length", str(len(data)))
    c.endheaders(data)
    r = c.getresponse()
    raw_body = r.read()
    try:
        return r.status, json.loads(raw_body)
    except ValueError:
        return r.status, raw_body


def snapshot(home):
    return {str(p): p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}


@pytest.fixture
def srv(server):  # noqa: F811
    return server


@pytest.fixture
def feat(server, ark_home, make_repo):  # noqa: F811
    make_repo("r")
    d = ark_home / "desk" / "r" / "s"
    d.mkdir(parents=True)
    (d / "spec.md").write_text(SPEC)
    (d / "plan.md").write_text(plan_text(repo="r", slug="s", spec="none"))
    return d


def sha(f):
    return hashlib.sha256(f.read_bytes()).hexdigest()


def review_all(d, doc):
    for s in reviews.load(d, doc)["sections"]:
        reviews.save(d, doc, s["key"], "ok", "")


def untouched_outside(before, after, d, ark_home, allowed=()):
    allowed_files = {str(d / n) for n in ("review.json", "review.json.tmp", "spec.md", "plan.md", "spec.md.tmp", "plan.md.tmp")}
    for p, m in after.items():
        if before.get(p) != m:
            ok = p in allowed_files or p.startswith(str(ark_home / "approvals")) or p.startswith(str(ark_home / "inbox")) \
                or any(p.startswith(str(a)) for a in allowed)
            assert ok, f"unexpected write: {p}"


@pytest.mark.parametrize("path", PATHS)
def test_every_write_needs_token_origin_host_and_a_json_object(srv, feat, path, ark_home):
    before = snapshot(ark_home)
    body = {"repo": "r", "slug": "s"}
    assert post(srv, path, body, token=None)[0] == 403
    assert post(srv, path, body, token="wrong")[0] == 403
    assert post(srv, path, body, origin="https://evil.example")[0] == 403
    assert post(srv, path, body, origin=None)[0] == 403
    assert post(srv, path, body, host="evil.example")[0] == 403
    assert post(srv, path, None, raw=b"[1]")[0] == 400
    assert post(srv, path, None, raw=b"not json")[0] == 400
    assert post(srv, path, None, raw=b"{" + b" " * 70000 + b"}")[0] == 400
    assert snapshot(ark_home) == before


def test_other_methods_stay_405(srv):
    from test_ui_server import get
    for m in ("PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        assert get(srv, "/api/review", method=m)[0] == 405


def test_review(srv, feat, ark_home):
    before = snapshot(ark_home)
    st, got = post(srv, "/api/review", {"repo": "r", "slug": "s", "doc": "spec", "key": "problem", "verdict": "ok", "note": "n"})
    assert st == 200 and got == reviews.load(feat, "spec")
    assert got["sections"][0]["verdict"] == "ok"
    assert post(srv, "/api/review", {"repo": "r", "slug": "s", "doc": "spec", "key": "nope", "verdict": "ok"})[0] == 400
    assert post(srv, "/api/review", {"repo": "r", "slug": "zz", "doc": "spec", "key": "problem", "verdict": "ok"})[0] == 404
    untouched_outside(before, snapshot(ark_home), feat, ark_home)


def test_approve_spec(srv, feat, ark_home):
    spec = feat / "spec.md"
    body = {"repo": "r", "slug": "s", "sha256": sha(spec)}
    st, got = post(srv, "/api/approve-spec", body)
    assert st == 409 and got["kind"] == "unreviewed"
    review_all(feat, "spec")
    st, got = post(srv, "/api/approve-spec", {**body, "sha256": "0" * 64})
    assert st == 409 and got["kind"] == "changed" and "error" in got
    before = snapshot(ark_home)
    assert post(srv, "/api/approve-spec", body)[0] == 200
    assert "status: approved" in spec.read_text()
    untouched_outside(before, snapshot(ark_home), feat, ark_home)


def _ready_plan(d):
    review_all(d, "plan")
    return {"repo": "r", "slug": "s", "sha256": sha(d / "plan.md")}


def test_approve_plan(srv, feat, ark_home):
    body = _ready_plan(feat)
    before = snapshot(ark_home)
    st, got = post(srv, "/api/approve-plan", body)
    assert st == 200, got
    assert got["repo"] == "r" and got["slug"] == "s"
    assert (ark_home / "approvals" / "r" / "s.json").exists() or list((ark_home / "approvals").rglob("*s*"))
    untouched_outside(before, snapshot(ark_home), feat, ark_home)


def test_approve_plan_refusals(srv, feat, ark_home, monkeypatch):
    body = {"repo": "r", "slug": "s", "sha256": sha(feat / "plan.md")}
    st, got = post(srv, "/api/approve-plan", body)
    assert st == 409 and got["kind"] == "unreviewed"
    body = _ready_plan(feat)
    st, got = post(srv, "/api/approve-plan", {**body, "sha256": "0" * 64})
    assert st == 409 and got["kind"] == "changed"
    monkeypatch.setenv("ARK_ROLE", "worker")
    assert post(srv, "/api/approve-plan", body)[1]["kind"] == "role"
    assert post(srv, "/api/approve-plan", body)[0] == 403
    monkeypatch.delenv("ARK_ROLE")
    assert not list((ark_home / "approvals").rglob("*.json"))


def test_approve_plan_needs_an_approved_spec_or_none(srv, feat, ark_home):
    (feat / "plan.md").write_text(plan_text(repo="r", slug="s"))  # spec: next to the plan, still draft
    body = _ready_plan(feat)
    st, got = post(srv, "/api/approve-plan", body)
    assert st in (409, 422) and not list((ark_home / "approvals").rglob("*.json"))


def test_approve_plan_invalid(srv, feat, ark_home):
    (feat / "plan.md").write_text(plan_text(repo="r", slug="s", spec="none", drop=("Goal",)))
    body = _ready_plan(feat)
    st, got = post(srv, "/api/approve-plan", body)
    assert st == 422 and got["kind"] == "invalid" and got["problems"]
    assert "status: draft" in (feat / "plan.md").read_text()


def live_run(ark_home, kind="worker", answered=None):
    st = run.new_state({"repo": "r", "slug": "s", "plan": "/p/plan.md", "sha256": "0" * 64,
                        "approved_at": "2026-11-05T10:00:00+00:00"})
    st["status"] = "running"
    q = questions.ask(st, kind, "which store?", job="x" if kind == "worker" else None)
    if answered:
        questions.resolve(st, q["id"], answered)
    run.save(st)
    return q["id"]


def ans(srv, qid, text="use the second option"):
    return post(srv, "/api/answer", {"repo": "r", "slug": "s", "id": qid, "text": text})


def test_answer_without_a_run(srv, feat):
    st, got = ans(srv, "q1")
    assert st == 404 and got["kind"] == "no-run"


def test_answer(srv, feat, ark_home):
    qid = live_run(ark_home)
    before = snapshot(ark_home)
    st, got = ans(srv, qid)
    assert st == 200 and got["id"] == qid
    untouched_outside(before, snapshot(ark_home), feat, ark_home)
    st, got = ans(srv, qid, "again")
    assert st == 409 and got["kind"] == "posted"
    assert ans(srv, "q99")[1]["kind"] == "unknown" and ans(srv, "q99")[0] == 404


def test_answer_closed_and_rejected(srv, feat, ark_home):
    qid = live_run(ark_home, answered="SQLite")
    st, got = ans(srv, qid)
    assert st == 409 and got["kind"] == "closed" and got["answer"]["answer"] == "SQLite"


def test_answer_rejected(srv, feat, ark_home):
    qid = live_run(ark_home, kind="cap_run")
    st, got = ans(srv, qid, "maybe")
    assert st == 422 and got["kind"] == "rejected"


def test_answer_role(srv, feat, monkeypatch):
    monkeypatch.setenv("ARK_ROLE", "worker")
    st, got = ans(srv, "q1")
    assert st == 403 and got["kind"] == "role"
