"""The runner reuses a green gate the worker's stop hook already ran on the same head (selective-gates-2 T5)."""
import json
import os
import time

import pytest
from helpers import approve, plan_text

from ark import gitops, loop, merge, paths, proc, run, steps

SLUG = "notes-edit"
SUMMARY = "ark-test: mode=job tests=7 (only what changed)"


@pytest.fixture(autouse=True)
def quiet_machine(monkeypatch):
    loop._load_wait.clear()
    monkeypatch.setattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0))


@pytest.fixture
def feature(runner, make_feature):
    def make():
        r = runner("demo")
        p = make_feature(plan_text(None, repo="demo", slug=SLUG), repo="demo", slug=SLUG, create_repo=False)
        approve(p, "demo", SLUG)
        return r
    return make


def state():
    return run.load("demo", SLUG)


def settled():
    st = state()
    return bool(st and st["jobs"]) and all(j["status"] in run.TERMINAL for j in st["jobs"].values()) \
        and not proc.active(paths.run_dir("demo", SLUG))


def drive(until, cycles=400):
    for _ in range(cycles):
        loop.cycle()
        if until():
            return
        time.sleep(0.05)
    raise AssertionError("not reached")


@pytest.fixture
def gates(monkeypatch):
    """The names of the gate processes the runner started."""
    started = []
    real = proc.start

    def start(run_dir, name, *a, **k):
        if name.endswith(".gate"):
            started.append(name)
        return real(run_dir, name, *a, **k)
    monkeypatch.setattr(proc, "start", start)
    return started


def with_receipt(monkeypatch, job="shell", **over):
    """As the worker's stop hook would: a receipt for the head the worker ends on, written as it finishes."""
    real = loop.on_worker
    seen = {}

    def on_worker(c, j, r):
        if j == job:
            receipt = {"head": gitops.rev(c.wt(j), "HEAD"), "gate": c.mjobs[j]["gate"], "ok": True,
                       "summary": SUMMARY, "at": "2026-11-23T10:00:00Z", **over}
            path = c.dir / f"{j}.workergate.json"
            path.write_text(receipt if isinstance(receipt, str) else json.dumps(receipt))
            seen["path"] = path
        return real(c, j, r)
    monkeypatch.setattr(loop, "on_worker", on_worker)
    monkeypatch.setitem(loop.HANDLERS, "worker", on_worker)
    return seen


def test_without_a_receipt_every_job_runs_its_gate(feature, gates):
    feature()
    drive(settled)
    assert sorted(gates) == ["shell.gate", "ui.gate"]
    assert merge.read_record(paths.run_dir("demo", SLUG), "shell", "gate").get("reused") is None


def test_a_matching_receipt_skips_the_gate_and_records_it_reused(feature, gates, monkeypatch):
    seen = with_receipt(monkeypatch)
    feature()
    drive(settled)
    st = state()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"]
    assert gates == ["ui.gate"]                                   # one gate run fewer
    rec = merge.read_record(paths.run_dir("demo", SLUG), "shell", "gate")
    assert rec["ok"] is True and rec["reused"] == "worker" and rec["summary"] == SUMMARY
    assert rec["head"] == st["jobs"]["shell"]["head"]
    assert rec["mode"] == "job" and rec["tests"] == 7
    assert not seen["path"].exists()                              # removed once read
    [row] = [r for r in steps.build(st) if r["kind"] == "gate" and r["job"] == "shell"]
    assert row["minutes"] == 0 and row["outcome"].startswith("green")


@pytest.mark.parametrize("over", [
    {"head": "0" * 40},                                           # another head
    {"gate": "pytest -q"},                                        # another gate command
    {"ok": False},
    "not json",                                                   # unreadable
])
def test_any_mismatch_runs_the_gate_as_today(feature, gates, monkeypatch, over):
    if isinstance(over, str):
        real = loop.on_worker

        def on_worker(c, j, r):
            (c.dir / f"{j}.workergate.json").write_text(over)
            return real(c, j, r)
        monkeypatch.setitem(loop.HANDLERS, "worker", on_worker)
        seen = None
    else:
        seen = with_receipt(monkeypatch, **over)
    feature()
    drive(settled)
    assert sorted(gates) == ["shell.gate", "ui.gate"]
    assert merge.read_record(paths.run_dir("demo", SLUG), "shell", "gate").get("reused") is None
    assert not (paths.run_dir("demo", SLUG) / "shell.workergate.json").exists()
    if seen:
        assert not seen["path"].exists()


def _ctx(tmp_path, j, gate="true"):
    class C:
        pass
    c = C()
    c.dir, c.st, c.mjobs = tmp_path, {"jobs": {"shell": j}}, {"shell": {"gate": gate}}
    return c


def test_a_regate_or_an_accepted_replacement_never_reuses(tmp_path):
    head = "a" * 40
    receipt = {"head": head, "gate": "true", "ok": True, "summary": SUMMARY}
    path = tmp_path / "shell.workergate.json"
    for j in ({"head": head, "regate": {"kind": "gate"}}, {"head": head, "accept_gate": "true"}):
        path.write_text(json.dumps(receipt))
        assert loop._worker_gate(_ctx(tmp_path, j), "shell") is None
        assert not path.exists()
    path.write_text(json.dumps(receipt))
    assert loop._worker_gate(_ctx(tmp_path, {"head": head}), "shell")["summary"] == SUMMARY
    assert loop._worker_gate(_ctx(tmp_path, {"head": head}), "shell") is None      # read once


def test_a_receipt_that_is_not_a_regular_file_is_not_followed(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"head": "a" * 40, "gate": "true", "ok": True}))
    (tmp_path / "shell.workergate.json").symlink_to(target)
    assert loop._worker_gate(_ctx(tmp_path, {"head": "a" * 40}), "shell") is None
    assert target.exists() and not (tmp_path / "shell.workergate.json").is_symlink()


@pytest.mark.parametrize("text, mode, tests", [
    (f"x\nark-test: mode=job (docs)\n...\n{SUMMARY}\n", "job", 7),
    ("ark-test: mode=full tests=1234 (base changed)", "full", 1234),
    ("12 passed", None, None),
    ("ark-test: mode=full (no list)", None, None),
])
def test_gate_summary(text, mode, tests):
    assert loop.gate_summary(text) == ({"mode": mode, "tests": tests} if mode else {})
