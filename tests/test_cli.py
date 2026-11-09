import json

import pytest

from ark import cli, paths
from ark.plan import load_plan, sha256_file
from helpers import plan_text


@pytest.fixture(autouse=True)
def _claude_session(monkeypatch):
    # No TTY under pytest: approve only runs inside a Claude Code session (CLAUDECODE=1).
    monkeypatch.setenv("CLAUDECODE", "1")


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_approve_closes_the_plan_and_writes_the_approval(make_feature, capsys):
    p = make_feature()
    code, out, _ = run(capsys, "approve", "demo/notes-edit")
    assert code == 0
    assert "Approved demo/notes-edit" in out
    assert "shell" in out and "Final PR" in out
    assert load_plan(p).frontmatter["status"] == "closed"
    rec = json.loads(paths.approval_file("demo", "notes-edit").read_text())
    assert rec["sha256"] == sha256_file(p)
    assert rec["plan"] == str(p.resolve())
    assert run(capsys, "validate", "demo/notes-edit", "--approved")[0] == 0


def test_approve_refuses_an_incomplete_manifest(make_feature, capsys):
    p = make_feature(plan_text(tasks=("T1", "T2", "T3", "T4")))
    code, _, err = run(capsys, "approve", "demo/notes-edit")
    assert code == 1
    assert "T4 is in no job" in err
    assert load_plan(p).frontmatter["status"] == "draft"
    assert not paths.approval_file("demo", "notes-edit").exists()


def test_approve_refuses_runner_processes(make_feature, capsys, monkeypatch):
    make_feature()
    monkeypatch.setenv("ARK_ROLE", "tick")
    code, _, err = run(capsys, "approve", "demo/notes-edit")
    assert code == 2
    assert "ARK_ROLE" in err
    assert not paths.approval_file("demo", "notes-edit").exists()


def test_an_edit_after_approval_makes_it_stale(make_feature, capsys):
    p = make_feature()
    run(capsys, "approve", "demo/notes-edit")
    p.write_text(p.read_text() + "\nOne more line.\n")
    code, out, _ = run(capsys, "validate", "demo/notes-edit", "--approved")
    assert code == 1
    assert "changed after approval" in out
    assert " stale " in run(capsys, "plans")[1]
    assert run(capsys, "approve", "demo/notes-edit")[0] == 0
    assert " ok " in run(capsys, "plans")[1]


def test_a_draft_plan_is_not_runnable(make_feature, capsys):
    make_feature()
    assert run(capsys, "validate", "demo/notes-edit", "--approved")[0] == 1


def test_status_groups_features_by_repo(make_feature, capsys):
    make_feature()
    make_feature(plan_text(slug="second"), slug="second", spec_status="draft")
    code, out, _ = run(capsys, "status")
    assert code == 0
    assert out.splitlines()[0] == "demo"
    assert "notes-edit" in out and "second" in out and "spec draft" in out


def test_status_on_an_empty_desk(ark_home, capsys):
    assert "Desk empty" in run(capsys, "status")[1]


def test_validate_accepts_a_path(make_feature, capsys):
    p = make_feature()
    code, out, _ = run(capsys, "validate", str(p))
    assert code == 0 and out.startswith("OK")


def test_approve_without_tty_outside_claude_code_is_refused(make_feature, capsys, monkeypatch):
    make_feature()
    monkeypatch.delenv("CLAUDECODE")
    code, _, err = run(capsys, "approve", "demo/notes-edit")
    assert code == 2 and "terminal" in err
    assert not paths.approval_file("demo", "notes-edit").exists()


def test_approve_refuses_a_plan_edited_during_confirmation(make_feature, capsys, monkeypatch):
    p = make_feature()
    original = p.read_text()

    def edit_then_yes(_prompt):
        p.write_text(original + "\nSneaky edit.\n")
        return "s"

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", edit_then_yes)
    code, _, err = run(capsys, "approve", "demo/notes-edit")
    assert code == 1 and "changed" in err
    assert load_plan(p).frontmatter["status"] == "draft"
    assert not paths.approval_file("demo", "notes-edit").exists()


@pytest.mark.parametrize("answer,ok", [("y", True), ("yes", True), ("s", True), ("si", True), ("sí", True),
                                       ("", False), ("n", False)])
def test_approve_prompt_is_in_english_and_takes_these_answers(make_feature, capsys, monkeypatch, answer, ok):
    p = make_feature()
    prompts = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or answer)
    code, out, _ = run(capsys, "approve", "demo/notes-edit")
    assert prompts == ["Approve this plan? [y/N] "]
    assert (code == 0) is ok
    assert ("Not approved." in out) is (not ok)
    assert (load_plan(p).frontmatter["status"] == "closed") is ok
