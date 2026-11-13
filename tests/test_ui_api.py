import json

import pytest
from helpers import plan_text
from test_ui_server import get, server  # noqa: F401  (the fixture)

from ark import drafts, ui, uidata


@pytest.fixture
def api(server, ark_home, make_repo, tmp_path, monkeypatch):
    make_repo("r")
    d = ark_home / "desk" / "r" / "s"
    d.mkdir(parents=True)
    (d / "spec.md").write_text("---\nstatus: draft\nrepo: r\nslug: s\ncreated: 2026-11-05\n---\n# S\n\n## Problem\n\nx\n")
    (d / "plan.md").write_text(plan_text(repo="r", slug="s"))
    (d / "before.png").write_bytes(b"\x89PNGdata")
    vend = tmp_path / "vendor"
    vend.mkdir()
    (vend / "md.mjs").write_text("export const x = 1;")
    monkeypatch.setattr(ui, "vendor_dir", lambda: vend)
    return server


def test_drafts_and_detail(api):
    status, ctype, body = get(api, "/api/drafts")
    assert status == 200 and ctype.startswith("application/json") and json.loads(body) == drafts.list_drafts()
    status, _, body = get(api, "/api/draft?repo=r&slug=s")
    assert status == 200 and json.loads(body) == json.loads(json.dumps(drafts.detail("r", "s")))


def test_a_bad_draft_is_404_json(api):
    for q in ("repo=r&slug=nope", "repo=..&slug=s", "repo=r", ""):
        status, ctype, body = get(api, "/api/draft?" + q)
        assert status == 404 and ctype.startswith("application/json") and "error" in json.loads(body)


def test_history_and_euklides(api):
    assert json.loads(get(api, "/api/history")[2]) == json.loads(json.dumps(uidata.history()))
    assert json.loads(get(api, "/api/euklides")[2]) == json.loads(json.dumps(uidata.euklides()))


def test_image(api):
    status, ctype, body = get(api, "/api/image?repo=r&slug=s&name=before.png")
    assert (status, ctype, body) == (200, "image/png", b"\x89PNGdata")
    assert get(api, "/api/image?repo=r&slug=s&name=../x.png")[0] == 404
    assert get(api, "/api/image?repo=r&slug=s&name=spec.md")[0] == 404


def test_vendor(api):
    status, ctype, body = get(api, "/vendor/spec-review/md.mjs")
    assert status == 200 and ctype.startswith("text/javascript") and body == b"export const x = 1;"
    assert get(api, "/vendor/spec-review/doc.mjs")[0] == 404  # allowed name, file absent
    assert get(api, "/vendor/spec-review/x.mjs")[0] == 404
    assert get(api, "/vendor/../lib/ark/ui.py")[0] == 404
    assert get(api, "/vendor/spec-review/%2e%2e/md.mjs")[0] == 404


def test_the_host_check_covers_every_get(api):
    for p in ("/", "/api/runs", "/api/drafts", "/api/draft?repo=r&slug=s", "/api/history", "/api/euklides",
              "/api/image?repo=r&slug=s&name=before.png", "/vendor/spec-review/md.mjs"):
        assert get(api, p, host="evil.example")[0] == 403, p
