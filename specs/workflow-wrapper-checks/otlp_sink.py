#!/usr/bin/env python3
"""Minimal OTLP/HTTP JSON receiver for check C5. Listens on 127.0.0.1:4318."""
import gzip
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path(os.environ.get("ARK_CHECKS_HOME", Path.home() / "ark-checks"))
OUT = HOME / "state" / "otlp.jsonl"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        try:
            payload = json.loads(body)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a") as fh:
            fh.write(json.dumps({"path": self.path, "payload": payload}) + "\n")
        print(self.path, len(body), "bytes", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("ARK_CHECKS_PORT", 4318))
    print(f"listening on 127.0.0.1:{port}, writing {OUT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
