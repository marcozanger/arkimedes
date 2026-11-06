"""ark-integrate (§5): squash a job head onto the current ark/<slug> in a scratch worktree and run
the plan's setup and integration_gate there. Prints one JSON line {ok, base, head, reason, tail};
the loop records it, and ark-merge only merges onto the exact base it was green for."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import gitops, merge, paths, run
from .plan import load_plan

GATE_TIMEOUT = 3600  # seconds, for setup and the gate together
TAIL = 3000  # characters of output kept for the worker


def _step(cmd: str, cwd, timeout: float) -> tuple[subprocess.Popen, str, bool]:
    """Run one shell command in its own process group: (process, tail of its output, timed out).
    Output goes to a file and only the command's own exit is waited for, so a daemon it left holding
    stdout or stderr cannot delay the result. The bytes need not be UTF-8."""
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen(["sh", "-c", cmd], cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT, start_new_session=True)
        try:
            p.wait(timeout=max(timeout, 0))
            timed_out = False
        except subprocess.TimeoutExpired:
            gitops._kill(p)
            timed_out = True
        size = os.fstat(out.fileno()).st_size
        n = TAIL * 4  # at most 4 bytes per character
        return p, os.pread(out.fileno(), n, max(size - n, 0)).decode("utf-8", "replace")[-TAIL:], timed_out


def integrate(repo_name: str, slug: str, job: str, head: str) -> dict:
    st = run.load(repo_name, slug)  # raises run.StateCorrupt on a damaged state; main() reports it
    repo = paths.find_repo(repo_name)
    if st is None or repo is None:
        raise ValueError(f"no run or no repo for {repo_name}/{slug}")
    reason = merge.state_reason(st) or merge.plan_reason(st)
    if reason:  # the gate and setup come from the plan: never run commands nobody approved
        return {"ok": False, "base": None, "head": head, "reason": reason, "tail": ""}
    m = load_plan(Path(st["plan"])).manifest
    gitops.fetch(repo)
    base = gitops.rev(repo, f"origin/ark/{slug}")
    out = {"ok": False, "base": base, "head": head, "reason": "", "tail": ""}
    scratch = paths.worktrees_dir() / repo_name / slug / f"_integrate-{job}"
    started = []
    try:
        if gitops.squash_keep(repo, base, head, f"integration check {job}", scratch) is None:
            return {**out, "reason": f"{job} does not merge cleanly onto ark/{slug}"}
        deadline = time.monotonic() + GATE_TIMEOUT
        setup = m.get("setup") or []
        for i, cmd in enumerate([*setup, m["integration_gate"]]):
            what = f"setup `{cmd}`" if i < len(setup) else "integration gate"
            p, tail, timed_out = _step(cmd, scratch, deadline - time.monotonic())
            started.append(p)
            if timed_out:
                return {**out, "reason": f"{what} timed out after {GATE_TIMEOUT}s", "tail": tail}
            if p.returncode:
                return {**out, "reason": f"{what} failed (exit {p.returncode})", "tail": tail}
        return {**out, "ok": True}
    finally:
        for p in started:  # what setup started stayed up for the gate; nothing outlives the check
            gitops._kill(p)
        gitops._cleanup(repo, scratch)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or "/" not in argv[0]:
        print("uso: ark-integrate <repo>/<slug> <job> <head-sha>", file=sys.stderr)
        return 2
    repo_name, _, slug = argv[0].rpartition("/")
    try:
        out = integrate(repo_name, slug, argv[1], argv[2])
    except Exception as e:  # the loop reads the JSON line; never leave it without one
        out = {"ok": False, "base": None, "head": argv[2], "reason": f"{type(e).__name__}: {e}", "tail": ""}
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
