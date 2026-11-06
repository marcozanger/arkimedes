"""ark-integrate (§5): squash a job head onto the current ark/<slug> in a scratch worktree and run
the plan's setup and integration_gate there. Prints one JSON line {ok, base, head, reason, tail};
the loop records it, and ark-merge only merges onto the exact base it was green for."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import gitops, paths, run
from .plan import load_plan


def integrate(repo_name: str, slug: str, job: str, head: str) -> dict:
    st = run.load(repo_name, slug)  # raises run.StateCorrupt on a damaged state; main() reports it
    repo = paths.find_repo(repo_name)
    if st is None or repo is None:
        raise ValueError(f"no run or no repo for {repo_name}/{slug}")
    m = load_plan(Path(st["plan"])).manifest
    gitops.fetch(repo)
    base = gitops.rev(repo, f"origin/ark/{slug}")
    out = {"ok": False, "base": base, "head": head, "reason": "", "tail": ""}
    scratch = paths.worktrees_dir() / repo_name / slug / f"_integrate-{job}"
    try:
        if gitops.squash_keep(repo, base, head, f"integration check {job}", scratch) is None:
            return {**out, "reason": f"{job} does not merge cleanly onto ark/{slug}"}
        for cmd in [*(m.get("setup") or []), m["integration_gate"]]:
            r = subprocess.run(["sh", "-c", cmd], cwd=scratch, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL)
            if r.returncode:
                what = "integration gate" if cmd == m["integration_gate"] else f"setup `{cmd}`"
                return {**out, "reason": f"{what} failed (exit {r.returncode})",
                        "tail": (r.stdout + r.stderr)[-3000:]}
        return {**out, "ok": True}
    finally:
        gitops.remove_worktree(repo, scratch)


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
