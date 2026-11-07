import json

import pytest

from ark import cli
from ark.plan import sha256_file
from helpers import plan_text


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_table_shows_jobs_totals_end_to_end_and_sha(make_feature, capsys):
    p = make_feature()
    code, out, _ = run(capsys, "estimate", "demo/notes-edit")
    assert code == 0
    assert "| shell | T1 | – | 30 min | $2.00 |" in out
    assert "| ui | T1 | shell | 30 min | $2.00 |" in out
    assert "| **total** | | | 1 h 00 min | $4.00 |" in out
    assert "Punta a punta: 1 h 00 min" in out
    assert sha256_file(p) in out


def test_json_matches_the_table(make_feature, capsys):
    p = make_feature()
    code, out, _ = run(capsys, "estimate", "demo/notes-edit", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["total_minutes"] == 60 and data["total_usd"] == 4 and data["end_to_end_minutes"] == 60
    assert data["writers"] == 3 and data["sha256"] == sha256_file(p)
    assert [j["id"] for j in data["jobs"]] == ["shell", "ui"]


def test_the_writer_limit_comes_from_workspace_json(make_feature, ark_home, capsys):
    ws = json.loads((ark_home / "workspace.json").read_text())
    ws["limits"] = {"writers_per_repo": 1}
    (ark_home / "workspace.json").write_text(json.dumps(ws))
    make_feature()
    data = json.loads(run(capsys, "estimate", "demo/notes-edit", "--json")[1])
    assert data["writers"] == 1


def test_an_invalid_plan_prints_the_problems(make_feature, capsys):
    make_feature(plan_text(tasks=("T1", "T2", "T3", "T4")))
    code, out, _ = run(capsys, "estimate", "demo/notes-edit")
    assert code == 1
    assert "T4 is in no job" in out
