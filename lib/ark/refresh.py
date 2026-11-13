"""ark-refresh: merge a main sha into the current ark/<slug> in a scratch worktree and run the plan's setup and
integration_gate there. Prints one JSON line {ok, base, main, merged, conflict, main_red, reason, tail}; nothing
is pushed. A red gate is checked on main alone, so a red main is told apart from a clash with the run's work."""
from __future__ import annotations

import os
import re
import signal
import sys
import time
from pathlib import Path

from . import gitops, merge, paths, run
from .integrate import GATE_TIMEOUT, STOPS, _exit_on_stop, _json_line, _step, base_check
from .plan import load_plan

_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def check(repo_name: str, slug: str, main_sha: str) -> dict:
    out = {"ok": False, "base": None, "main": main_sha, "merged": None, "conflict": [], "main_red": None,
           "reason": "", "tail": ""}
    st = run.load(repo_name, slug)  # raises run.StateCorrupt on a damaged state; main() reports it
    repo = paths.find_repo(repo_name)
    if st is None or repo is None:
        raise ValueError(f"no run or no repo for {repo_name}/{slug}")
    reason = merge.state_reason(st) or merge.plan_reason(st)
    if reason:  # the gate and setup come from the plan: never run commands nobody approved
        return {**out, "reason": reason}
    # These names build a path that gets deleted: only what this run's state says.
    if (repo_name, slug) != (st["repo"], st["slug"]):
        return {**out, "reason": f"this run is {st['repo']}/{st['slug']}"}
    m = load_plan(Path(st["plan"])).manifest
    gitops.fetch(repo)
    base = gitops.rev(repo, f"origin/ark/{slug}")
    out["base"] = base
    try:
        if not _FULL_SHA.fullmatch(main_sha or "") or gitops.rev(repo, main_sha) != main_sha:
            raise gitops.GitError("not a full sha")
    except gitops.GitError:
        return {**out, "reason": f"main {main_sha!r} is not a commit of {repo_name} (a full sha is needed)"}
    scratch = paths.worktrees_dir() / repo_name / slug / "_refresh-check"
    started = []
    alive_r, alive_w = os.pipe()
    try:
        merged, conflict = gitops.merge_keep(repo, base, main_sha, f"refresh: merge main {main_sha[:12]}", scratch)
        if merged is None:
            return {**out, "conflict": conflict, "reason": f"main does not merge cleanly into ark/{slug}"}
        out["merged"] = merged
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
                if i >= len(setup):  # the gate itself: is main red on its own?
                    scratch_main = paths.worktrees_dir() / repo_name / slug / "_refresh-main"
                    res["main_red"] = base_check(repo, main_sha, scratch_main, setup, m["integration_gate"],
                                                 time.monotonic() + GATE_TIMEOUT, alive_r)
                return res
        return {**out, "ok": True}
    finally:
        for p in started:  # what setup started stayed up for the gate; nothing outlives the check
            gitops._kill(p)
        os.close(alive_r)
        os.close(alive_w)
        gitops._cleanup(repo, scratch)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or "/" not in argv[0]:
        print("uso: ark-refresh <repo>/<slug> <main-sha>", file=sys.stderr)
        return 2
    repo_name, _, slug = argv[0].rpartition("/")
    before = {sig: signal.signal(sig, _exit_on_stop) for sig in STOPS}
    try:
        out = check(repo_name, slug, argv[1])
    except Exception as e:  # the loop reads the JSON line; never leave it without one
        out = {"ok": False, "base": None, "main": argv[1], "merged": None, "conflict": [], "main_red": None,
               "reason": f"{type(e).__name__}: {e}", "tail": ""}
    for sig, handler in before.items():  # not on the way out of a stop: those signals stay ignored
        signal.signal(sig, handler)
    print(_json_line(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
