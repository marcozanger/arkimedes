import json

import pytest
from helpers import plan_text

from ark import approve, paths
from ark.plan import sha256_file


@pytest.fixture
def valid_plan(make_feature):
    return make_feature(plan_text())


@pytest.fixture
def invalid_plan(make_feature):
    return make_feature(plan_text(drop=("Run manifest",)))


def test_approves_a_valid_plan(valid_plan):
    sha = sha256_file(valid_plan)
    rec = approve.approve_plan(valid_plan, expect_sha=sha)
    assert rec["sha256"] == sha256_file(valid_plan)
    assert "status: closed" in valid_plan.read_text()
    assert json.loads(paths.approval_file(rec["repo"], rec["slug"]).read_text())["sha256"] == rec["sha256"]


def test_a_changed_plan_is_not_approved(valid_plan):
    with pytest.raises(approve.ApproveError) as e:
        approve.approve_plan(valid_plan, expect_sha="0" * 64)
    assert e.value.kind == "changed"
    assert "status: draft" in valid_plan.read_text()


def test_an_invalid_plan_is_not_approved(invalid_plan):
    with pytest.raises(approve.ApproveError) as e:
        approve.approve_plan(invalid_plan)
    assert e.value.kind == "invalid" and e.value.problems


def test_a_runner_process_cannot_approve(valid_plan, monkeypatch):
    monkeypatch.setenv("ARK_ROLE", "worker")
    with pytest.raises(approve.ApproveError) as e:
        approve.approve_plan(valid_plan)
    assert e.value.kind == "role"
    assert not paths.approvals_dir().exists() or not any(paths.approvals_dir().rglob("*.json"))
