import http.client
import json
import threading
from pathlib import Path

import pytest

from ark import approve, ui, versions

PAGE = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def two_versions(make_feature):
    p = make_feature()
    approve.approve_plan(p)
    p.write_text(p.read_text().replace("status: closed", "status: draft").replace("Edit and delete notes.", "Edit notes."))
    approve.approve_plan(p)
    return p


def test_payload_lists_versions_and_the_last_diff(make_feature):
    two_versions(make_feature)
    out = versions.payload("demo", "notes-edit")
    assert [v["version"] for v in out["versions"]] == [1, 2]
    assert out["diff"]["from"] == 1 and out["diff"]["to"] == 2
    assert out["diff"]["spec"] is not None and "+Edit notes." in out["diff"]["plan"]
    assert out["jobs"]  # per-job changes of the last version


def test_payload_with_one_version_has_no_diff(make_feature):
    p = make_feature()
    approve.approve_plan(p)
    assert versions.payload("demo", "notes-edit")["diff"] is None


def test_the_versions_route_serves_the_payload(ark_home, make_feature, tmp_path, monkeypatch):
    two_versions(make_feature)
    page = tmp_path / "index.html"
    page.write_text("<title>x</title>")
    monkeypatch.setattr(ui, "page_file", lambda: page)
    srv = ui.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", "/api/plan/demo/notes-edit/versions?from=1&to=2")
        r = c.getresponse()
        body = json.loads(r.read())
        assert r.status == 200 and len(body["versions"]) == 2 and body["diff"]["to"] == 2
        c.request("GET", "/api/plan/demo/notes-edit/versions?from=x")
        r = c.getresponse()
        r.read()
        assert r.status == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_plan_screen_has_a_version_picker_and_diff():
    html = PAGE.read_text()
    assert 'id="plan-version"' in html
    assert 'id="plan-diff"' in html
    assert "/versions" in html
    assert "diff-add" in html and "diff-del" in html
