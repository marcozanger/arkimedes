"""ark-merge: the only way a job PR reaches ark/<slug> (§7). Every check must hold.

The loop calls merge() in-process and records the result in the run state; `ark-merge` runs the
same function from a terminal (it does not change the run state)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import forge as forge_mod, gitops, paths, run
from .globs import match_any
from .plan import load_plan, sha256_file


class MergeRefused(Exception):
    pass


def record(run_dir, job: str, kind: str, data: dict) -> None:
    """A gate / verdict / integrate result, written by the runner (never by a model)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / f"{job}.{kind}.json.tmp"
    tmp.write_text(json.dumps({**data, "at": run.now()}, indent=2), encoding="utf-8")
    tmp.replace(run_dir / f"{job}.{kind}.json")


def read_record(run_dir, job: str, kind: str) -> dict | None:
    try:
        return json.loads((Path(run_dir) / f"{job}.{kind}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def manifest_jobs(st: dict) -> dict:
    return {j["id"]: j for j in load_plan(Path(st["plan"])).manifest["jobs"]}


def check(st: dict, job: str, forge, repo) -> str | None:
    """None when the job's PR may be merged into ark/<slug>; otherwise the first reason it may not."""
    if os.environ.get("ARK_ROLE") in ("worker", "reviewer"):
        return "workers and reviewers never merge"
    slug = st["slug"]
    base = f"ark/{slug}"
    d = paths.run_dir(st["repo"], slug)
    try:
        approved = json.loads(paths.approval_file(st["repo"], slug).read_text(encoding="utf-8"))["sha256"]
        sha = sha256_file(st["plan"])
    except (OSError, ValueError, KeyError):
        return "no readable approval or plan"
    if sha != approved or sha != st["plan_sha"]:
        return "the plan changed after its approval"
    mjobs = manifest_jobs(st)
    if job not in mjobs or job not in st["jobs"]:
        return f"job {job} is not in the approved manifest"
    j = st["jobs"][job]
    if not j.get("pr") or not j.get("head"):
        return "the job has no PR or head yet"
    v = forge.view(repo, j["pr"]["number"])
    if v["state"] != "OPEN":
        return f"the PR is {v['state']}"
    if v["base"] != base:
        return f"the PR base is {v['base']}, not {base}"
    if v["head"] != f"{base}-{job}":
        return f"the PR head branch is {v['head']}, not {base}-{job}"
    if v["head_sha"] != j["head"]:
        return "the PR head moved after it was gated and reviewed"
    gitops.fetch(repo)
    outside = [f for f in gitops.changed_files(repo, f"origin/{base}", j["head"]) if not match_any(f, mjobs[job]["area"])]
    if outside:
        return "files outside the job's area: " + ", ".join(outside[:5])
    g = read_record(d, job, "gate")
    if not g or not g.get("ok") or g.get("head") != j["head"]:
        return "no green job gate for this head"
    r = read_record(d, job, "verdict")
    if not r or r.get("verdict") != "CLEAN" or r.get("head") != j["head"]:
        return "no CLEAN review verdict for this head"
    i = read_record(d, job, "integrate")
    if not i or not i.get("ok") or i.get("head") != j["head"] or i.get("base") != gitops.rev(repo, f"origin/{base}"):
        return f"no green integration gate for this head on the current {base}"
    return None


def merge(st: dict, job: str, forge, repo) -> None:
    reason = check(st, job, forge, repo)
    if reason:
        raise MergeRefused(reason)
    j = st["jobs"][job]
    forge.merge(repo, j["pr"]["number"], subject=manifest_jobs(st)[job]["pr_title"], head_sha=j["head"])


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or "/" not in argv[0]:
        print("uso: ark-merge <repo>/<slug> <job>", file=sys.stderr)
        return 2
    repo_name, _, slug = argv[0].rpartition("/")
    try:
        st = run.load(repo_name, slug)
    except run.StateCorrupt as e:  # a damaged state is reported, never read as "no run" or replaced
        print(f"FAIL {e}")
        return 1
    repo = paths.find_repo(repo_name)
    if st is None or repo is None:
        print("FAIL no run or no repo")
        return 1
    try:
        merge(st, argv[1], forge_mod.get(), repo)
    except (MergeRefused, forge_mod.ForgeError, gitops.GitError) as e:
        print(f"FAIL {e}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
