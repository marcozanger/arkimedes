"""`ark ui`: the control center page, the runs snapshot and the page's five writes, on 127.0.0.1 only.

GET serves the page (with the session token), the JSON endpoints, draft images and the vendored renderer.
POST writes only a feature's review.json, its spec status, a plan approval, a run answer and a verdict on a
run's decisions (reviews.jsonl), through the same
code the terminal commands use; each needs the Host, the Origin and the `X-Ark-Token` header to match."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import answers, approve, drafts, monitor, paths, plan as plan_mod, reviews, runreview, uidata, validate, versions

PORT = 8737
MAX_BODY = 65536
PLACEHOLDER = b"{{ARK_TOKEN}}"
VENDOR = {"md.mjs", "decisions.mjs", "fold.mjs", "doc.mjs", "strings.mjs"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml"}
TOKEN = ""   # the token of the last server made; each server keeps its own as `srv.token`
JSON_TYPE = "application/json; charset=utf-8"


class HttpError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


def page_file() -> Path:
    return paths.code_dir() / "ui" / "index.html"


def vendor_dir() -> Path:
    return paths.code_dir() / "ui" / "vendor" / "spec-review"


def _q(query: dict, name: str) -> str:
    v = query.get(name)
    return v[0] if v else ""


def _slugs(body: dict) -> tuple[str, str]:
    repo, slug = body.get("repo"), body.get("slug")
    if not (isinstance(repo, str) and isinstance(slug, str) and drafts.REPO.match(repo) and drafts.SLUG.match(slug)):
        raise HttpError(400, "bad repo or slug")
    return repo, slug


def _folder(body: dict) -> Path:
    repo, slug = _slugs(body)
    try:
        return drafts.folder(repo, slug)
    except drafts.DraftError as e:
        raise HttpError(404, str(e))


def _all_ok(folder: Path, doc: str) -> bool:
    secs = reviews.load(folder, doc)["sections"]
    return bool(secs) and all(s["verdict"] == "ok" for s in secs)


def _expect_sha(body: dict) -> str:
    sha = body.get("sha256")
    if not isinstance(sha, str):
        raise HttpError(400, "sha256 must be a string")
    return sha


def _post_review(body: dict) -> dict:
    d = _folder(body)
    note = body.get("note") or ""
    try:
        reviews.save(d, body.get("doc"), body.get("key"), body.get("verdict"), note)
    except reviews.ReviewError as e:
        raise HttpError(400, str(e))
    return reviews.load(d, body["doc"])


def _post_approve_spec(body: dict) -> dict:
    if "ARK_ROLE" in os.environ:
        raise HttpError(403, "a runner process (ARK_ROLE) cannot approve", kind="role")
    d = _folder(body)
    sha, spec = _expect_sha(body), d / "spec.md"
    try:
        now = hashlib.sha256(spec.read_bytes()).hexdigest()
    except OSError:
        raise HttpError(404, "no spec.md in this draft")
    if now != sha:
        raise HttpError(409, "the spec changed since it was shown; look at it again.", kind="changed")
    if not _all_ok(d, "spec"):
        raise HttpError(409, "every section of the spec must be reviewed ok first.", kind="unreviewed")
    try:
        plan_mod.set_status(spec, "approved")
    except (plan_mod.PlanError, OSError) as e:
        raise HttpError(422, str(e), kind="invalid")
    return {"status": "approved", "sha256": hashlib.sha256(spec.read_bytes()).hexdigest()}


def _post_approve_plan(body: dict) -> dict:
    if "ARK_ROLE" in os.environ:
        raise HttpError(403, "a runner process (ARK_ROLE) cannot approve; only Marco approves plans.", kind="role")
    d = _folder(body)
    sha, plan_file = _expect_sha(body), d / "plan.md"
    try:
        if plan_mod.sha256_file(plan_file) != sha:
            raise HttpError(409, "the plan changed since it was shown; look at it again.", kind="changed")
    except OSError:
        raise HttpError(404, "no plan.md in this draft")
    problems = [str(p) for p in validate.validate(plan_file)]
    if problems:
        raise HttpError(422, "the plan does not meet the contract.", kind="invalid", problems=problems)
    try:
        spec_ref = plan_mod.split_frontmatter(plan_file.read_text(encoding="utf-8"))[0].get("spec")
    except (plan_mod.PlanError, OSError):
        spec_ref = None
    if spec_ref != "none":
        try:
            spec_status = plan_mod.split_frontmatter((d / "spec.md").read_text(encoding="utf-8"))[0].get("status")
        except (plan_mod.PlanError, OSError):
            spec_status = None
        if spec_status != "approved":
            raise HttpError(409, "approve the spec first.", kind="unreviewed")
    if not _all_ok(d, "plan"):
        raise HttpError(409, "every section of the plan must be reviewed ok first.", kind="unreviewed")
    try:
        return approve.approve_plan(plan_file, expect_sha=sha)
    except approve.ApproveError as e:
        if e.kind == "role":
            raise HttpError(403, str(e), kind="role")
        if e.kind == "changed":
            raise HttpError(409, str(e), kind="changed")
        if e.kind == "invalid":
            raise HttpError(422, str(e), kind="invalid", problems=e.problems)
        raise HttpError(404, str(e), kind=e.kind)


ANSWER_STATUS = {"role": 403, "no-run": 404, "unknown": 404, "closed": 409, "posted": 409, "rejected": 422}


def _post_answer(body: dict) -> dict:
    repo, slug = _slugs(body)
    qid, text = body.get("id"), body.get("text")
    if not isinstance(qid, str) or not isinstance(text, str):
        raise HttpError(400, "id and text must be strings")
    try:
        return answers.post(repo, slug, qid, text)
    except answers.AnswerError as e:
        extra = {"answer": e.answer} if e.answer is not None else {}
        raise HttpError(ANSWER_STATUS.get(e.kind, 400), str(e), kind=e.kind, **extra)


def _post_decision_review(body: dict) -> dict:
    if "ARK_ROLE" in os.environ:
        raise HttpError(403, "a runner process (ARK_ROLE) cannot review decisions", kind="role")
    repo, slug = _slugs(body)
    key, verdict, comment = body.get("key"), body.get("verdict"), body.get("comment") or ""
    if not isinstance(key, str) or not isinstance(verdict, str):
        raise HttpError(400, "key and verdict must be strings")
    try:
        return runreview.save(repo, slug, key, verdict, comment)
    except runreview.RunReviewError as e:
        raise HttpError(404 if e.kind == "unknown" else 400, str(e), kind=e.kind)


POSTS = {"/api/review": _post_review, "/api/approve-spec": _post_approve_spec,
         "/api/approve-plan": _post_approve_plan, "/api/answer": _post_answer,
         "/api/decision-review": _post_decision_review}


class Handler(BaseHTTPRequestHandler):
    server_version = "ark-ui"

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, msg: str) -> None:
        self._send(status, "text/plain; charset=utf-8", (msg + "\n").encode())

    def _json(self, status: int, obj) -> None:
        self._send(status, JSON_TYPE, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._text(403, "forbidden host")
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            self._get(path, query)
        except HttpError as e:
            self._json(e.status, {"error": str(e), **e.extra})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _get(self, path: str, query: dict) -> None:
        if path == "/":
            f = page_file()
            try:
                body = f.read_bytes()
            except OSError:
                return self._text(500, f"cannot read the page {f}")
            return self._send(200, "text/html; charset=utf-8", body.replace(PLACEHOLDER, self.server.token.encode()))
        if path == "/api/runs":
            return self._json(200, monitor.build())
        if path == "/api/drafts":
            return self._json(200, drafts.list_drafts())
        if path == "/api/draft":
            try:
                return self._json(200, drafts.detail(_q(query, "repo"), _q(query, "slug")))
            except drafts.DraftError as e:
                raise HttpError(404, str(e))
        if path == "/api/decision-review":
            repo, slug = _slugs({"repo": _q(query, "repo"), "slug": _q(query, "slug")})
            return self._json(200, runreview.load(repo, slug))
        if path == "/api/history":
            return self._json(200, uidata.history())
        if path == "/api/euklides":
            return self._json(200, uidata.euklides())
        if path == "/api/image":
            name = _q(query, "name")
            try:
                data = drafts.images(_q(query, "repo"), _q(query, "slug"), name)
            except drafts.DraftError as e:
                raise HttpError(404, str(e))
            return self._send(200, IMAGE_TYPES.get(Path(name).suffix.lower(), "application/octet-stream"), data)
        m = re.fullmatch(r"/api/plan/(.+)/([^/]+)/versions", path)
        if m:
            try:
                a, b = (int(_q(query, k)) if _q(query, k) else None for k in ("from", "to"))
            except ValueError:
                raise HttpError(400, "from and to must be version numbers")
            repo, slug = _slugs({"repo": m.group(1), "slug": m.group(2)})
            return self._json(200, versions.payload(repo, slug, a, b))
        m = re.fullmatch(r"/vendor/spec-review/([^/]+)", path)
        if m and m.group(1) in VENDOR:
            try:
                data = (vendor_dir() / m.group(1)).read_bytes()
            except OSError:
                raise HttpError(404, "not found")
            return self._send(200, "text/javascript; charset=utf-8", data)
        if path.startswith("/api/") or path.startswith("/vendor/"):
            raise HttpError(404, "not found")
        self._text(404, "not found")

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._text(403, "forbidden host")
        port = self.server.server_address[1]
        if self.headers.get("Origin") not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return self._json(403, {"error": "forbidden origin"})
        token = self.headers.get("X-Ark-Token") or ""
        if not hmac.compare_digest(token.encode("utf-8", "replace"), self.server.token.encode()):
            return self._json(403, {"error": "forbidden token"})
        handler = POSTS.get(urlsplit(self.path).path)
        if handler is None:
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self._json(400, {"error": "Content-Length is required"})
        if n < 0 or n > MAX_BODY:
            return self._json(400, {"error": f"the body is over {MAX_BODY} bytes"})
        try:
            body = json.loads(self.rfile.read(n))
        except (ValueError, RecursionError):
            return self._json(400, {"error": "the body is not JSON"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "the body must be a JSON object"})
        try:
            self._json(200, handler(body))
        except HttpError as e:
            self._json(e.status, {"error": str(e), **e.extra})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _no(self) -> None:
        self._text(405, "method not allowed")

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _no

    def log_message(self, *a) -> None:
        pass


def make_server(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    global TOKEN
    srv.token = TOKEN = secrets.token_urlsafe(32)
    return srv


def cmd_ui(args) -> int:
    try:
        srv = make_server(args.port)
    except OSError as e:
        print(f"ark ui: cannot use port {args.port} ({e.strerror or e}); pick another with --port", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"Control center: {url}  (Ctrl-C stops it)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
