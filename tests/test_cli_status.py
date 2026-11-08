import json
from datetime import datetime, timedelta, timezone

from ark import cli, paths, run
from helpers import approve


def test_status_line_has_status_elapsed_eta(ark_home, make_feature, capsys):
    p = make_feature()
    approve(p)
    s = run.new_state(json.loads(paths.approval_file("demo", "notes-edit").read_text()))
    s.update(status="running", started=(datetime.now(timezone.utc) - timedelta(minutes=72)).isoformat(timespec="seconds"))
    s["jobs"] = {"shell": run.new_job({"id": "shell", "tier": "T1"}), "ui": run.new_job({"id": "ui", "tier": "T1"})}
    run.save(s)
    assert cli.main(["status", "demo"]) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "notes-edit" in ln)
    assert "status running" in line and "elapsed 1h12m" in line and "eta ~1h00m" in line
    assert " run " not in out
