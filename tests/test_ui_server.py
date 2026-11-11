import http.client
import json
import socket
import threading

import pytest

from ark import monitor, ui


@pytest.fixture
def server(ark_home, tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<!doctype html><title>Arkimedes</title>")
    monkeypatch.setattr(ui, "page_file", lambda: page)
    srv = ui.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def get(srv, path, method="GET", host=None):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{port}")
    c.endheaders()
    r = c.getresponse()
    return r.status, r.getheader("Content-Type") or "", r.read()


def test_it_binds_only_loopback(server):
    assert server.server_address[0] == "127.0.0.1"


def test_root_serves_the_page(server):
    status, ctype, body = get(server, "/?x=1")
    assert status == 200 and ctype.startswith("text/html") and b"<title>Arkimedes</title>" in body


def test_api_runs_is_the_snapshot(server):
    status, ctype, body = get(server, "/api/runs")
    got, want = json.loads(body), monitor.build()
    assert status == 200 and ctype.startswith("application/json")
    assert {k: v for k, v in got.items() if k != "generated"} == {k: v for k, v in want.items() if k != "generated"}


def test_other_paths_methods_and_hosts(server):
    port = server.server_address[1]
    assert get(server, "/index.html")[0] == 404
    assert get(server, "/api/runs", method="POST")[0] == 405
    assert get(server, "/api/runs", host="evil.example")[0] == 403
    assert get(server, "/", host=f"localhost:{port}")[0] == 200


def test_a_busy_port_names_it(capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        assert ui.cmd_ui(type("A", (), {"port": port, "open": False})()) == 2
    finally:
        s.close()
    err = capsys.readouterr().err
    assert str(port) in err and "--port" in err
