import json

from ark import cli, paths
from ark.plan import load_plan, sha256_file
from helpers import plan_text


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_approve_closes_the_plan_and_writes_the_approval(make_feature, capsys):
    p = make_feature()
    code, out, _ = run(capsys, "approve", "demo/notes-edit")
    assert code == 0
    assert "Aprobado demo/notes-edit" in out
    assert "shell" in out and "PR final" in out
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
    assert "Desk vacío" in run(capsys, "status")[1]


def test_validate_accepts_a_path(make_feature, capsys):
    p = make_feature()
    code, out, _ = run(capsys, "validate", str(p))
    assert code == 0 and out.startswith("OK")
