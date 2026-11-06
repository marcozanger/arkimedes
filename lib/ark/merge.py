"""ark-merge: the only way a job PR reaches ark/<slug> (§7). Every check must hold.

The loop calls merge() in-process and records the result in the run state; `ark-merge` runs the
same function from a terminal (it does not change the run state).

merge() returning means one thing: the forge shows the PR merged, at the head that was gated and
reviewed. The exit code of the merge call says nothing either way. Everything else raises."""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

from . import forge as forge_mod, gitops, paths, run
from .globs import match_any
from .plan import PlanError, load_plan, sha256_file


NOT_ACTIVE = ("waiting", "held", "done")  # a run marked `blocked` is active: §5, "the rest continues"
LOCK_WAIT = 30  # seconds merge() waits for another merge of the same run


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
        data = json.loads((Path(run_dir) / f"{job}.{kind}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def manifest_jobs(st: dict) -> dict:
    return {j["id"]: j for j in load_plan(Path(st["plan"])).manifest["jobs"]}


def state_reason(st: dict) -> str | None:
    """The fields every check relies on; a damaged state is a reason, not an exception."""
    if (not all(isinstance(st.get(k), str) and st[k] for k in ("repo", "slug", "plan", "plan_sha"))
            or not isinstance(st.get("jobs"), dict)):
        return "the run state is malformed"
    return None


def plan_reason(st: dict) -> str | None:
    """None when the plan on disk is the one that was approved and that this run started from."""
    try:
        approved = json.loads(paths.approval_file(st["repo"], st["slug"]).read_text(encoding="utf-8"))["sha256"]
        sha = sha256_file(st["plan"])
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return "no readable approval or plan"
    if sha != approved or sha != st["plan_sha"]:
        return "the plan changed after its approval"
    return None


def _role_reason() -> str | None:
    if os.environ.get("ARK_ROLE") in ("worker", "reviewer"):
        return "workers and reviewers never merge"
    return None


def _pr_and_head(st: dict, job: str) -> tuple[int, str] | None:
    """The job's PR number and head from the state, when both are there and well formed."""
    j = st["jobs"].get(job)
    pr = j.get("pr") if isinstance(j, dict) else None
    number = pr.get("number") if isinstance(pr, dict) else None
    head = j.get("head") if isinstance(j, dict) else None
    if isinstance(number, bool) or not isinstance(number, int) or not isinstance(head, str) or not head:
        return None
    return number, head


def check(st: dict, job: str, forge, repo) -> str | None:
    """None when the job's PR may be merged into ark/<slug>; otherwise the first reason it may not."""
    reason = _role_reason() or state_reason(st) or plan_reason(st)
    if reason:
        return reason
    if st.get("status") in NOT_ACTIVE:
        return f"the run is {st['status']}"
    slug = st["slug"]
    base = f"ark/{slug}"
    d = paths.run_dir(st["repo"], slug)
    try:
        mjobs = manifest_jobs(st)
    except (PlanError, KeyError, TypeError):
        return "no readable approval or plan"
    if job not in mjobs or job not in st["jobs"]:
        return f"job {job} is not in the approved manifest"
    j = st["jobs"][job]
    if not isinstance(j, dict):
        return "the run state is malformed"
    status = j.get("status")
    if isinstance(status, str) and status in run.TERMINAL:
        return f"the job is {status}"
    for dep in mjobs[job].get("after") or []:
        other = st["jobs"].get(dep)
        if not isinstance(other, dict) or other.get("status") != "merged":
            return f"the job waits for {dep}"
    ref = _pr_and_head(st, job)
    if not ref:
        return "the job has no PR or head yet"
    number, head = ref
    v = forge.view(repo, number)
    if v["state"] != "OPEN":
        return f"the PR is {v['state']}"
    if v["base"] != base:
        return f"the PR base is {v['base']}, not {base}"
    if v["head"] != f"{base}-{job}":
        return f"the PR head branch is {v['head']}, not {base}-{job}"
    if v["head_sha"] != head:
        return "the PR head moved after it was gated and reviewed"
    gitops.fetch(repo)
    changed = gitops.changed_files(repo, f"origin/{base}", head)
    if not changed:
        return f"the job changes nothing against {base}"
    outside = [f for f in changed if not match_any(f, mjobs[job]["area"])]
    if outside:
        return "files outside the job's area: " + ", ".join(outside[:5])
    g = read_record(d, job, "gate")
    if not g or g.get("ok") is not True or g.get("head") != head:
        return "no green job gate for this head"
    r = read_record(d, job, "verdict")
    if not r or r.get("verdict") != "CLEAN" or r.get("head") != head:
        return "no CLEAN review verdict for this head"
    i = read_record(d, job, "integrate")
    if not i or i.get("ok") is not True or i.get("head") != head or i.get("base") != gitops.rev(repo, f"origin/{base}"):
        return f"no green integration gate for this head on the current {base}"
    return None


def _merged_at(v: dict, st: dict, job: str, head: str) -> bool:
    """This PR, merged into this run's integration branch, at exactly this head."""
    base = f"ark/{st['slug']}"
    return v["state"] == "MERGED" and v["base"] == base and v["head"] == f"{base}-{job}" and v["head_sha"] == head


def confirmed(st: dict, job: str, forge, repo) -> bool:
    """The forge shows this job's PR already merged at its head: an earlier call did it, even if that
    call ended in an error or a crash."""
    ref = _pr_and_head(st, job)
    return bool(ref) and _merged_at(forge.view(repo, ref[0]), st, job, ref[1])


def _merge_locked(st: dict, job: str, forge, repo) -> None:
    if confirmed(st, job, forge, repo):
        return
    ref = _pr_and_head(st, job)
    reason = check(st, job, forge, repo)
    if reason:
        raise MergeRefused(reason)
    number, head = ref
    failure = None
    try:
        forge.merge(repo, number, subject=manifest_jobs(st)[job]["pr_title"], head_sha=head)
    except Exception as e:  # the forge decides below: it may have merged before this call failed
        failure = e
    said = str(failure) if failure else "the merge call succeeded"
    try:
        v = forge.view(repo, number)
    except Exception as e:
        raise forge_mod.ForgeError(f"the outcome of the merge of PR {number} is unknown ({said}); "
                                   f"the next call looks again: {e}") from e
    if _merged_at(v, st, job, head):
        return
    if failure:
        raise forge_mod.ForgeError(said) from failure
    raise forge_mod.ForgeError(f"the merge call succeeded, but PR {number} is {v['state']} at {v['head_sha']}, "
                               f"not merged at {head}")


def _take_lock(lock) -> None:
    """A holder that is stopped (Ctrl-Z on a terminal ark-merge) holds the lock with no time limit, and
    the loop calls merge() in-process: wait a bounded time, then let the next call retry."""
    end = time.monotonic() + LOCK_WAIT
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= end:
                raise forge_mod.ForgeError(f"another process has held the merge lock for {LOCK_WAIT}s") from None
            time.sleep(0.1)


def merge(st: dict, job: str, forge, repo) -> None:
    """Merge the job's PR, or confirm an earlier merge of it. Returns only when the forge shows it
    merged at the job's head; raises MergeRefused, ForgeError or GitError otherwise. A per-run lock
    keeps another merge from moving ark/<slug> between the checks and the merge."""
    reason = _role_reason() or state_reason(st)
    if reason:
        raise MergeRefused(reason)
    d = paths.run_dir(st["repo"], st["slug"])
    try:
        d.mkdir(parents=True, exist_ok=True)
        lock = open(d / "merge.lock", "a")
    except OSError as e:
        raise MergeRefused(f"cannot take the merge lock: {e}") from e
    with lock:
        _take_lock(lock)
        _merge_locked(st, job, forge, repo)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or "/" not in argv[0]:
        print("uso: ark-merge <repo>/<slug> <job>", file=sys.stderr)
        return 2
    repo_name, _, slug = argv[0].rpartition("/")
    try:
        st = run.load(repo_name, slug)  # a damaged state is reported, never read as "no run" or replaced
        repo = paths.find_repo(repo_name)
        if st is None or repo is None:
            print("FAIL no run or no repo")
            return 1
        merge(st, argv[1], forge_mod.get(), repo)
    except (MergeRefused, forge_mod.ForgeError, gitops.GitError, run.StateCorrupt) as e:
        print(f"FAIL {e}")
        return 1
    except Exception as e:  # the same promise in a terminal: a FAIL line, not a traceback
        print(f"FAIL {type(e).__name__}: {e}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
