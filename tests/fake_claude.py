#!/usr/bin/env python3
"""Stand-in for `claude -p` in the runner tests. It acts by ARK_ROLE:

worker    appends "attempt <n>" to <area[0] with ** -> <job>.txt> (or outside/<job>.txt when the job
          is in FAKE_OUTSIDE), commits, writes the report, ends STATUS: DONE. FAKE_FAIL=job:n,...
          makes attempt n end FAILED. FAKE_SLEEP=seconds delays it. FAKE_STATE/max_active records
          the most workers alive at once.
reviewer  FIX on the first review of a job in FAKE_FIX, CLEAN otherwise.
tick      the decisions file when asked for one, else a judge answer `retry`.

FAKE_DOWN=1 makes every role end at once with exit 1 and no result line, as an unreachable model
does; FAKE_STATE/down.count counts those.
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]


def opt(name):
    return args[args.index(name) + 1] if name in args else None


PROMPT = opt("-p") or ""
SESSION = opt("--session-id") or opt("--resume")
ROLE = os.environ.get("ARK_ROLE")
STATE = Path(os.environ["FAKE_STATE"])


def bump(name):
    f = STATE / f"{name}.count"
    n = int(f.read_text()) + 1 if f.exists() else 1
    f.write_text(str(n))
    return n


def track(delta):
    with open(STATE / "active.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        f, m = STATE / "active", STATE / "max_active"
        n = (int(f.read_text()) if f.exists() else 0) + delta
        f.write_text(str(n))
        if n > (int(m.read_text()) if m.exists() else 0):
            m.write_text(str(n))


def listed(var, job):
    return job in os.environ.get(var, "").split(",")


def emit(result, stream=True):
    if stream:
        print(json.dumps({"type": "system", "subtype": "init", "session_id": SESSION}))
    print(json.dumps({"type": "result", "result": result, "session_id": SESSION,
                      "total_cost_usd": 0.01, "is_error": False}))


if os.environ.get("FAKE_DOWN"):
    bump("down")
    print("Could not reach the model")
    sys.exit(1)

if ROLE == "worker":
    job, wt = os.environ["ARK_JOB"], Path(os.environ["ARK_WORKTREE"])
    track(1)
    try:
        n = bump(f"{job}.worker")
        time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
        if f"{job}:{n}" in os.environ.get("FAKE_FAIL", "").split(","):
            emit("tests are red\nSTATUS: FAILED — tests are red")
            sys.exit(0)
        area = json.loads(os.environ["ARK_AREA"])
        rel = f"outside/{job}.txt" if listed("FAKE_OUTSIDE", job) else area[0].replace("**", f"{job}.txt")
        f = wt / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a") as h:
            h.write(f"attempt {n}\n")
        subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"{job} attempt {n}"], cwd=wt, check=True)
        Path(os.environ["ARK_REPORT"]).write_text(
            f"## What I did\n\n{job} attempt {n}.\n\n## Tests\n\nnone\n\n## DECISIONS\n\n"
            f"- Wrote {rel}. Why: the fake always does. Rejected: nothing.\n\n## Open issues\n\nnone\n")
        emit("done\nSTATUS: DONE")
    finally:
        track(-1)
elif ROLE == "reviewer":
    job = os.environ["ARK_JOB"]
    if listed("FAKE_FIX", job) and bump(f"{job}.review") == 1:
        emit('one problem\n{"verdict": "FIX", "findings": ["src/x:1 — fix it"]}')
    else:
        emit('{"verdict": "CLEAN", "findings": []}')
elif ROLE == "tick":
    if "Write the decisions file" in PROMPT:
        emit("# Decisions — fake\n\n## shell — merged\n\n- **D1** · a fake decision · _arkimedes_\n", stream=False)
    else:
        emit('{"action": "retry", "note": "try again"}', stream=False)
else:
    sys.exit(f"fake claude: unknown ARK_ROLE {ROLE!r}")
