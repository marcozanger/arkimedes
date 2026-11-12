"""ark-integrate (§5): squash a job head onto the current ark/<slug> in a scratch worktree and run
the plan's setup and integration_gate there. Prints one JSON line {ok, base, head, reason, tail};
the loop records it, and ark-merge only merges onto the exact base it was green for."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import gitops, merge, paths, run
from .plan import load_plan

GATE_TIMEOUT = 3600  # seconds, for setup and the gate together
TAIL = 3000  # characters of output kept for the worker
REASON = 500  # characters of a reason in the JSON line
MAX_LINE = 3900  # characters of the JSON line: see _json_line
STOPS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

# A step runs in its own session, so nothing sent to this process's group reaches it. The first line
# ties the step's group to this process: the pipe's end of file means ark-integrate is gone, however it
# went (SIGKILL included). The extra subshell makes it no child of the command, which replaces this
# shell: neither a `wait` nor a program waiting until it has no children waits for it.
STEP = '( (read _ <&{fd}; kill -9 0) >/dev/null 2>&1 & )\nexec sh -c "$1"'


def _step(cmd: str, cwd, timeout: float, alive_fd: int) -> tuple[subprocess.Popen, str, bool]:
    """Run one shell command in its own process group: (process, tail of its output, timed out).
    Output goes to a file and only the command's own exit is waited for, so a daemon it left holding
    stdout or stderr cannot delay the result. The bytes need not be UTF-8. `alive_fd` is the read end
    of a pipe only this process can write to."""
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen(["sh", "-c", STEP.format(fd=alive_fd), "ark-integrate-step", cmd], cwd=cwd,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                             start_new_session=True, pass_fds=(alive_fd,))
        try:
            p.wait(timeout=max(timeout, 0))
            timed_out = False
        except subprocess.TimeoutExpired:
            gitops._kill(p)
            timed_out = True
        except BaseException:  # a stop: the step dies before its worktree is removed
            gitops._kill(p)
            raise
        size = os.fstat(out.fileno()).st_size
        n = TAIL * 4  # at most 4 bytes per character
        return p, os.pread(out.fileno(), n, max(size - n, 0)).decode("utf-8", "replace")[-TAIL:], timed_out


def base_check(repo, base: str, scratch: Path, setup: list[str], gate: str, deadline: float, alive_r) -> bool | None:
    """Whether the integration gate is red on the base without the job: True, False, or None when it could not
    tell (a setup step red, a timeout, no checkout)."""
    try:
        if gitops.squash_keep(repo, base, base, "base check", scratch) is None:
            return None
        for cmd in setup:
            p, _, timed_out = _step(cmd, scratch, deadline - time.monotonic(), alive_r)
            if timed_out or p.returncode != 0:
                return None
        p, _, timed_out = _step(gate, scratch, deadline - time.monotonic(), alive_r)
        return None if timed_out else p.returncode != 0
    except gitops.GitError:
        return None
    finally:
        gitops._cleanup(repo, scratch)


def integrate(repo_name: str, slug: str, job: str, head: str) -> dict:
    st = run.load(repo_name, slug)  # raises run.StateCorrupt on a damaged state; main() reports it
    repo = paths.find_repo(repo_name)
    if st is None or repo is None:
        raise ValueError(f"no run or no repo for {repo_name}/{slug}")
    reason = merge.state_reason(st) or merge.plan_reason(st)
    if reason:  # the gate and setup come from the plan: never run commands nobody approved
        return {"ok": False, "base": None, "head": head, "reason": reason, "tail": ""}
    m = load_plan(Path(st["plan"])).manifest
    # These names build a path that gets deleted: only what the validated plan and this run's state say.
    if (repo_name, slug) != (st["repo"], st["slug"]):
        return {"ok": False, "base": None, "head": head, "reason": f"this run is {st['repo']}/{st['slug']}", "tail": ""}
    if job not in st["jobs"] or job not in {j["id"] for j in m["jobs"]}:
        return {"ok": False, "base": None, "head": head, "reason": f"job {job} is not in the approved manifest",
                "tail": ""}
    gitops.fetch(repo)
    base = gitops.rev(repo, f"origin/ark/{slug}")
    out = {"ok": False, "base": base, "head": head, "reason": "", "tail": ""}
    scratch = paths.worktrees_dir() / repo_name / slug / f"_integrate-{job}"
    started = []
    alive_r, alive_w = os.pipe()
    try:
        if gitops.squash_keep(repo, base, head, f"integration check {job}", scratch) is None:
            return {**out, "reason": f"{job} does not merge cleanly onto ark/{slug}"}
        deadline = time.monotonic() + GATE_TIMEOUT
        setup = m.get("setup") or []
        for i, cmd in enumerate([*setup, m["integration_gate"]]):
            what = f"setup `{cmd}`" if i < len(setup) else "integration gate"
            p, tail, timed_out = _step(cmd, scratch, deadline - time.monotonic(), alive_r)
            started.append(p)
            if timed_out:
                return {**out, "reason": f"{what} timed out after {GATE_TIMEOUT}s", "tail": tail}
            if p.returncode:
                res = {**out, "reason": f"{what} failed (exit {p.returncode})", "tail": tail}
                if i >= len(setup):  # the gate itself: is it red without the job too?
                    scratch_base = paths.worktrees_dir() / repo_name / slug / f"_integrate-{job}-base"
                    res["base_red"] = base_check(repo, base, scratch_base, setup, m["integration_gate"],
                                                 time.monotonic() + GATE_TIMEOUT, alive_r)
                return res
        return {**out, "ok": True}
    finally:
        for p in started:  # what setup started stayed up for the gate; nothing outlives the check
            gitops._kill(p)
        os.close(alive_r)
        os.close(alive_w)
        gitops._cleanup(repo, scratch)


def _exit_on_stop(signum, frame) -> None:
    """Exit through integrate's `finally`. Only once: a stop signals twice (proc.stop, then the watcher
    once the wrapper is gone), and a second exit would interrupt the cleanup of the first."""
    for sig in STOPS:
        signal.signal(sig, signal.SIG_IGN)
    raise SystemExit(128 + signum)


def _json_line(out: dict) -> str:
    """The line must reach the loop whole, and proc keeps only the last 4000 characters of a program's
    output: non-ASCII text grows up to six times when escaped, so what gets cut is the oldest of the tail."""
    out = {**out, "reason": out["reason"][:REASON]}
    line = json.dumps(out)
    while len(line) > MAX_LINE and out["tail"]:
        out["tail"] = out["tail"][len(out["tail"]) // 2 + 1:]
        line = json.dumps(out)
    return line


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or "/" not in argv[0]:
        print("uso: ark-integrate <repo>/<slug> <job> <head-sha>", file=sys.stderr)
        return 2
    repo_name, _, slug = argv[0].rpartition("/")
    before = {sig: signal.signal(sig, _exit_on_stop) for sig in STOPS}
    try:
        out = integrate(repo_name, slug, argv[1], argv[2])
    except Exception as e:  # the loop reads the JSON line; never leave it without one
        out = {"ok": False, "base": None, "head": argv[2], "reason": f"{type(e).__name__}: {e}", "tail": ""}
    for sig, handler in before.items():  # not on the way out of a stop: those signals stay ignored
        signal.signal(sig, handler)
    print(_json_line(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
