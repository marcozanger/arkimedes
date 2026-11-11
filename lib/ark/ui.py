"""`ark ui`: the control center page and the runs snapshot, on 127.0.0.1 only. Read-only."""
from __future__ import annotations

import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import monitor, paths

PORT = 8737


def page_file() -> Path:
    return paths.code_dir() / "ui" / "index.html"


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

    def do_GET(self) -> None:
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return self._text(403, "forbidden host")
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            f = page_file()
            try:
                body = f.read_bytes()
            except OSError:
                return self._text(500, f"cannot read the page {f}")
            return self._send(200, "text/html; charset=utf-8", body)
        if path == "/api/runs":
            try:
                body = json.dumps(monitor.build(), ensure_ascii=False).encode("utf-8")
            except Exception as e:
                err = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False).encode("utf-8")
                return self._send(500, "application/json; charset=utf-8", err)
            return self._send(200, "application/json; charset=utf-8", body)
        self._text(404, "not found")

    def _no(self) -> None:
        self._text(405, "method not allowed")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _no

    def log_message(self, *a) -> None:
        pass


def make_server(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


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
