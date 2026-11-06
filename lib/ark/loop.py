"""ark-loop: the always-on runner (§4, §5). No model: a state machine over files.

Each cycle: every approval → its run (runs/<repo>/<slug>/state.json); start waiting runs; hold a
run whose plan changed; consume process receipts; move every job forward within the limits.
Claude runs only as workers, reviewers and two read-only ticks (judge, decisions)."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import signal
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import brief, claude, forge as forge_mod, gitops, integrate, merge, paths, proc, run
from .globs import areas_overlap, match_any
from .plan import load_plan, sha256_file
from .validate import validate

ERROR_ALERT_AFTER = 20    # cycles (about 5 minutes) a step keeps failing before Marco hears about it
HOLDERS_PATIENCE = 900    # seconds a job waits for leftovers in its worktree before it blocks
INFRA_QUICK = 120         # seconds: a model process that dies sooner, with no answer, never got to work
INFRA_LIMIT = 200         # tries before a job stops waiting for the model; about 50 hours at the ceiling
BACKOFF_FIRST = 15        # seconds before the first retry; it doubles each time
BACKOFF_MAX = 900         # seconds, the ceiling of that wait
REPORT_MAX = 1_000_000    # bytes of a worker's report the runner reads
DECISIONS_TICKS = 2       # paid ticks a run's close may start before the runner writes the fallback itself
SKIP_ALERT_AFTER = 3      # integration checks that could not run before Marco hears about it

# What a job's status says is running, and where the job goes back to if that process is not there.
RUNNING = {"working": "worker", "gating": "gate", "reviewing": "review", "integrating": "integrate", "judging": "judge"}
WAITING = {"worker": "needs_worker", "gate": "needs_gate", "review": "needs_review",
           "integrate": "needs_integrate", "judge": "needs_judge"}
CHECK_KINDS = ("gate", "review", "integrate")  # what a new approval makes a job earn again
REWOUND = ("gating", "needs_pr", "needs_review", "reviewing", "needs_integrate", "integrating", "needs_merge")

_said: set[tuple] = set()  # what was already alerted about a run with no state to remember it in


def _clean(x):
    """Text from a model or a worker ends up in argv, in briefs and in the state: a NUL or a lone surrogate
    there raises on every cycle, for ever. Anything that comes from them passes through here first."""
    if isinstance(x, str):
        return x.replace("\0", "").encode("utf-8", "replace").decode("utf-8")
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if isinstance(x, dict):
        return {_clean(k): _clean(v) for k, v in x.items()}
    return x


def _read_untrusted(path: Path) -> str | None:
    """A worker can write in the run folder and its bytes are not trusted: only a regular file counts (a
    symlink could read anything, a FIFO would hang the whole loop), and only its first REPORT_MAX bytes."""
    try:
        return _clean(paths.read_regular(path, REPORT_MAX).decode("utf-8", "replace"))
    except OSError:
        return None


def approvals() -> list[dict]:
    out = []
    for f in paths.approvals_dir().glob("**/*.json"):
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(a, dict) and all(isinstance(a.get(k), str) for k in ("repo", "slug", "plan", "sha256", "approved_at")):
            out.append(a)
    return sorted(out, key=lambda a: a["approved_at"])


def ticks_dir() -> Path:
    """cwd of tick processes: outside every repo and outside ~/.claude/arkimedes (no project CLAUDE.md)."""
    d = paths.worktrees_dir() / "_ticks"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Ctx:
    def __init__(self, st: dict, forge):
        self.st, self.forge = st, forge
        self.plan = load_plan(Path(st["plan"]))
        self.mjobs = {j["id"]: j for j in self.plan.manifest["jobs"]}
        self.repo = paths.find_repo(st["repo"])
        if self.repo is None:
            raise gitops.GitError(f"repo {st['repo']} not found under a workspace root")
        self.dir = paths.run_dir(st["repo"], st["slug"])
        self.base = f"ark/{st['slug']}"

    def wt(self, job: str) -> Path:
        return paths.worktrees_dir() / self.st["repo"] / self.st["slug"] / job

    def branch(self, job: str) -> str:
        return f"{self.base}-{job}"

    def report(self, job: str) -> Path:
        return self.dir / f"{job}.report.md"

    def read_report(self, job: str) -> str | None:
        return _read_untrusted(self.report(job))


# ---- runs -------------------------------------------------------------------------------------

def _branch_names(slug: str, jobs) -> dict[str, str]:
    """Every branch a run writes, and what it is to that run."""
    return {f"ark/{slug}": "integration branch", **{f"ark/{slug}-{j}": f"branch of job {j}" for j in jobs}}


def _started_runs(st: dict, others: list[dict]) -> list[tuple[str, str, str, list | None]]:
    """(repo, slug, status, jobs) of every other run that may still write a branch. From the disk too, since a
    cycle's list leaves out two kinds that go on once repaired: a run with no approval, and one whose state cannot be
    read (jobs None). Only a state that says it finished is taken for finished. A waiting run has written no job
    branch, and it passes try_start before it writes anything, so it is the one that stops."""
    idle = ("waiting", "done", "blocked")
    out = [(o["repo"], o["slug"], o["status"], list(o["jobs"])) for o in others if o is not st and o["status"] not in idle]
    seen = {(o["repo"], o["slug"]) for o in others} | {(st["repo"], st["slug"])}
    root = paths.runs_dir()
    for f in sorted(root.glob("**/state.json")):
        rel = f.parent.relative_to(root)
        key = (str(rel.parent), rel.name)
        if key in seen:
            continue
        try:
            o = _load(*key)
        except Exception:
            out.append((*key, "unreadable", None))
            continue
        if o and o["status"] not in idle:
            out.append((*key, o["status"], list(o["jobs"])))
    return out


def _same_origin(repo: Path, other: str) -> bool:
    """Two checkouts of one origin share its branches. What cannot be told counts as the same."""
    there = paths.find_repo(other)
    try:
        return there is None or gitops.origin_url(repo) == gitops.origin_url(there)
    except gitops.GitError:
        return True


def _branch_collision(st: dict, jobs: list, repo: Path, others: list[dict]) -> str:
    """Why this run may not start: ark/<slug>-<job> can spell another run's branch ("foo" with job "bar", and "foo-bar"),
    and a push onto it would land on that run's branch with no gate. Whether the branch is on origin yet says nothing:
    a job branch appears only when its worker ends. Only the run about to start can stop without losing work."""
    mine = _branch_names(st["slug"], jobs)
    for o_repo, slug, status, o_jobs in _started_runs(st, others):
        if o_jobs is None:  # its jobs are unknown, but its names are not: ark/<slug> and ark/<slug>-*
            theirs = {b: "possible branch" for b in mine if b == f"ark/{slug}" or b.startswith(f"ark/{slug}-")}
        else:
            theirs = _branch_names(slug, o_jobs)
        clash = sorted(set(mine) & set(theirs))
        if clash and (o_repo == st["repo"] or _same_origin(repo, o_repo)):
            b = clash[0]
            return (f"branch collision: {b} would be this plan's {mine[b]}, and it is the {theirs[b]} of the {status} plan "
                    f"{o_repo}/{slug}. Nothing is pushed. Approve under another slug, or let that plan end and delete the branch")
    return ""


def try_start(st: dict, others: list[dict]) -> bool:
    """waiting → running: the approved plan still validates, the repo is there, none of its branches is a
    branch of another started run, no running run in the same repo has an overlapping area, ark/<slug> and
    its job branches do not exist yet. Then push ark/<slug>. The only way into "running", and one loop runs
    at a time, so two started runs never share a branch: a new approval cannot change a started run's jobs."""
    plan = Path(st["plan"])
    problems = validate(plan, approved=True)
    reason, repo, default, area, existing, normal, mjobs = "", None, None, [], None, False, []
    if problems:
        reason = f"the plan does not validate: {problems[0].message}"
    else:
        repo = paths.find_repo(st["repo"])
        mjobs = load_plan(plan).manifest["jobs"]  # once: what is checked is what starts
        area = [g for j in mjobs for g in j["area"]]
        if repo is None:
            reason = f"repo {st['repo']} not found under a workspace root"
    if not reason:  # before the overlap: that wait ends by itself and says nothing, and this one needs Marco
        reason = _branch_collision(st, [j["id"] for j in mjobs], repo, others)
    if not reason:
        for o in others:
            if o is not st and o["repo"] == st["repo"] and o["status"] in ("running", "held") \
                    and areas_overlap(area, o.get("area") or []):
                reason, normal = f"its area overlaps the {o['status']} plan {o['slug']}", o["status"] == "running"
                break
    if not reason:
        try:
            gitops.fetch(repo)
            default = gitops.default_branch(repo)
            existing = gitops.remote_sha(repo, f"ark/{st['slug']}")
            if existing and existing != st["base_sha"]:
                reason = f"ark/{st['slug']} already exists on origin: delete it, or approve under another slug"
            for job in () if reason else mjobs:
                branch = f"ark/{st['slug']}-{job['id']}"
                if gitops.remote_sha(repo, branch):  # a job's push could never fast-forward over it
                    reason = f"{branch} already exists on origin: delete it, or approve under another slug"
                    break
        except gitops.GitError as e:
            reason = str(e)
    if reason:
        if reason != st["reason"]:
            st["reason"] = reason  # remembered, so he hears each reason once; only a running plan's overlap ends by itself
            (run.event if normal else run.alert)(st, f"waiting: {reason}")
        return False
    if existing:  # this run pushed it and died before saving "running"
        base = existing
    else:
        base = gitops.rev(repo, f"origin/{default}")
        st.update(default_branch=default, base_sha=base)
        run.save(st)  # on disk before the branch exists: the next cycle knows that branch is ours
        gitops.push_ref(repo, base, f"ark/{st['slug']}")
    st.update(status="running", reason="", default_branch=default, base_sha=base, area=area)
    for mj in mjobs:
        st["jobs"][mj["id"]] = run.new_job(mj)
    run.event(st, f"started: ark/{st['slug']} from {default} @ {base[:8]}")
    return True


# ---- job transitions ---------------------------------------------------------------------------

def fail(c: Ctx, job: str, why: str) -> None:
    j, why = c.st["jobs"][job], _clean(why)
    j.update(status="needs_judge", failure=why, tier_attempts=j["tier_attempts"] + 1)
    run.event(c.st, f"{job} failed ({j['tier']}, attempt {j['tier_attempts']} at this tier): {why}")


def _block(st: dict, job: str, why: str) -> None:
    why = _clean(why)
    st["jobs"][job].update(status="blocked", reason=why)
    run.alert(st, f"{job} blocked: {why}")


def block(c: Ctx, job: str, why: str) -> None:
    _block(c.st, job, why)


def allowed_actions(j: dict) -> list[str]:
    """block always; retry while fewer than 2 failures at this tier; escalate below T2."""
    out = ["block"]
    if j["tier_attempts"] < 2:
        out.append("retry")
    if j["tier"] in claude.NEXT_TIER:
        out.append("escalate")
    return out


def _error(c: Ctx, job: str, e: Exception) -> None:
    """A git / gh error while moving a job: it stays where it is and is retried next cycle, since a laptop
    offline must not block anything. Marco hears about it once, if it does not go away. Any other error
    is not known to heal, and a job retrying it for ever is a job nobody looks at, so that one blocks."""
    text = _clean(f"{type(e).__name__}: {e}")
    j = c.st["jobs"][job]
    j["errors"] = j.get("errors", 0) + 1
    if j.get("error") != text:
        j["error"] = text
        run.event(c.st, f"{job}: {text} (retrying)")
    if j["errors"] >= ERROR_ALERT_AFTER and not isinstance(e, (gitops.GitError, forge_mod.ForgeError)):
        return block(c, job, f"the same step failed for {ERROR_ALERT_AFTER} cycles: {text}")
    if j["errors"] == ERROR_ALERT_AFTER:
        run.alert(c.st, f"{job} has been failing for {ERROR_ALERT_AFTER} cycles, still retrying: {text}")


# ---- receipts ---------------------------------------------------------------------------------

def _answer(r: dict):
    return _clean(brief.parse_json_line(r["result"]))


def _clean_exit(r: dict) -> bool:
    return not r["reason"] and r["exit"] == 0 and not r.get("is_error")


def _printable(name: str) -> str:
    """A file name from git may hold undecodable bytes, and the run state must stay writable."""
    return name.encode("utf-8", "backslashreplace").decode("utf-8")


def on_worker(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    j.update(resume_msg=None, note=None)  # kept until now: a worker that never ran still needs them
    j["session"] = r.get("session")  # only a process that says it had one can be resumed
    if not r["reason"]:  # any reason may mean setup never finished: it runs again
        j["setup_done"] = True
    if r["reason"]:
        return fail(c, job, r["reason"])
    if not _clean_exit(r):
        return fail(c, job, f"the worker ended with an error (exit {r['exit']})")
    status, detail = brief.worker_status(r["result"])
    if status != "DONE":
        return fail(c, job, f"the worker ended {status}: {detail}")
    wt = c.wt(job)
    if not gitops.is_clean(wt):
        return fail(c, job, "the worker ended DONE with uncommitted changes")
    files = gitops.changed_files(wt, f"origin/{c.base}", "HEAD")
    if not files:
        return fail(c, job, "the worker ended DONE without any change")
    outside = [_printable(f) for f in files if not match_any(f, c.mjobs[job]["area"])]
    if outside:
        return block(c, job, "changed files outside its area: " + ", ".join(outside[:5]))
    j.update(status="needs_push", head=gitops.rev(wt, "HEAD"))
    run.event(c.st, f"{job}: worker done, head {j['head'][:8]}")


def on_gate(c: Ctx, job: str, r: dict) -> None:
    ok = r["exit"] == 0 and not r["reason"]
    tail = _clean(r["result"] or "")[-2000:]
    merge.record(c.dir, job, "gate", {"ok": ok, "head": r["meta"].get("head"), "exit": r["exit"], "tail": tail})
    if ok:
        c.st["jobs"][job]["status"] = "needs_pr"
        run.event(c.st, f"{job}: job gate green")
    else:
        fail(c, job, f"job gate red (exit {r['exit']}{', ' + r['reason'] if r['reason'] else ''}): {tail[-600:]}")


def _review_answer(r: dict) -> tuple[str, list] | None:
    """(verdict, findings) from an answer of exactly the agreed shape; None for anything else."""
    d = _answer(r) if _clean_exit(r) else None
    if not isinstance(d, dict) or set(d) != {"verdict", "findings"}:
        return None
    verdict, findings = d["verdict"], d["findings"]
    if not isinstance(verdict, str) or verdict not in ("CLEAN", "FIX") or not isinstance(findings, list):
        return None
    if verdict == "FIX" and not findings:  # nothing to send the worker back with
        return None
    return verdict, findings


def on_review(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    answer = _review_answer(r)
    if answer is None:
        j["review_errors"] += 1
        if j["review_errors"] >= 2:
            return block(c, job, "the reviewer gave no verdict twice")
        j["status"] = "needs_review"
        run.event(c.st, f"{job}: the reviewer gave no verdict; reviewing again")
        return
    j["review_errors"] = 0
    verdict, findings = answer[0], [str(f) for f in answer[1]][:20]
    merge.record(c.dir, job, "verdict", {"verdict": verdict, "head": r["meta"].get("head"), "findings": findings})
    if verdict == "CLEAN":
        j["status"] = "needs_integrate"
        run.event(c.st, f"{job}: review CLEAN")
        return
    j["review_rounds"] += 1
    run.event(c.st, f"{job}: review FIX (round {j['review_rounds']}): {len(findings)} findings")
    if j["review_rounds"] >= 3:
        return fail(c, job, "3 review rounds without CLEAN: " + "; ".join(findings)[:600])
    j["resume_msg"] = brief.resume_message("review", "\n".join(f"- {f}" for f in findings), c.st["slug"])
    j["status"] = "needs_worker"


def _integrate_again(c: Ctx, job: str, counter: str) -> int:
    j = c.st["jobs"][job]
    j[counter] = j.get(counter, 0) + 1
    j["status"] = "needs_integrate"
    return j[counter]


def on_integrate(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    out = None if r["reason"] else _answer(r)
    if not isinstance(out, dict) or "ok" not in out:  # a stop or a death: nothing was learned about the job
        why = r["reason"] or "ark-integrate gave no result"
        if _integrate_again(c, job, "integrate_lost") >= 2:
            return block(c, job, f"ark-integrate ended twice without a result: {why}")
        run.event(c.st, f"{job}: ark-integrate ended without a result ({why}); checking again")
        return
    ok = out["ok"] is True and isinstance(out.get("base"), str)
    reason = out.get("reason", "")
    if not ok and out.get("base") is None:  # the check never ran: not the worker's doing
        j["status"] = "needs_integrate"
        n = _wait_longer(j, "integrate_skips")  # no slot while it waits: the next heavy job is not starved
        if n >= INFRA_LIMIT:
            return block(c, job, f"the integration check could not run, {n} times: {reason}")
        if n == 1:
            run.event(c.st, f"{job}: the integration check could not run: {reason}")
        if n == SKIP_ALERT_AFTER:
            run.alert(c.st, f"{job}: the integration check could not run, {n} times: {reason}")
        return
    j["integrate_lost"] = j["integrate_skips"] = 0
    j.pop("not_before", None)
    merge.record(c.dir, job, "integrate", {"ok": ok, "base": out.get("base"),
                                           "head": out.get("head") or r["meta"].get("head"),
                                           "reason": reason, "tail": out.get("tail", "")})
    if ok:
        j["status"] = "needs_merge"
        run.event(c.st, f"{job}: integration gate green on {out['base'][:8]}")
        return
    j["integrate_fails"] += 1
    if j["integrate_fails"] >= 2:
        return block(c, job, f"integration gate failed twice: {reason}")
    j["resume_msg"] = brief.resume_message("integrate", f"{reason}\n\n{out.get('tail', '')}", c.st["slug"])
    j["status"] = "needs_worker"
    run.event(c.st, f"{job}: integration gate red ({reason}); back to the worker")


def _judge_answer(r: dict) -> tuple[object, str]:
    """(action, note) from an answer of exactly the agreed shape; (None, "") for anything else."""
    d = _answer(r) if _clean_exit(r) else None
    if not isinstance(d, dict) or set(d) != {"action", "note"} or not isinstance(d["note"], str):
        return None, ""
    return d["action"], d["note"].strip()[:1000]


def on_judge(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    allowed = allowed_actions(j)  # from the job as it is now: an action outside the list never runs
    action, note = _judge_answer(r)
    if not isinstance(action, str) or action not in allowed:
        action = "retry" if "retry" in allowed else "escalate" if "escalate" in allowed else "block"
        note = f"the judge gave no usable answer; the runner chose {action}"
    run.event(c.st, f"{job} judge: {action} — {note}")
    if action == "block":
        return block(c, job, note or j["failure"])
    if action == "escalate":
        j["tier"] = claude.NEXT_TIER[j["tier"]]
        j["tier_path"].append(j["tier"])
        j.update(tier_attempts=0, session=None, resume_msg=None, review_rounds=0)
    elif j["session"]:
        j["resume_msg"] = brief.resume_message("judge", f"{j['failure']}\n\nNote: {note}", c.st["slug"])
    j.update(note=note, status="needs_worker")


DECISIONS = "_decisions.tick"  # the close-of-run tick belongs to the run, not to a job
MODEL_KINDS = ("worker", "review", "judge")  # the others run a program of ours, not the model
HANDLERS = {"worker": on_worker, "gate": on_gate, "review": on_review, "integrate": on_integrate, "judge": on_judge}


def _usd(value) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return value if ok else 0


def _wait_longer(j: dict, counter: str) -> int:
    """Count one more try and hold the job back, doubling up to the ceiling. Returns the count."""
    n = j[counter] = j.get(counter, 0) + 1
    j["not_before"] = time.time() + min(BACKOFF_MAX, BACKOFF_FIRST * 2 ** (n - 1))
    return n


def _learned_nothing(r: dict) -> bool:
    """A model process that died right away with no answer and no cost: offline, auth expired, model
    down. Not knowing how long it lived, or a log with more than claude's start-up in it, counts as having
    worked, so the job is charged as usual."""
    try:
        quick = r["end"] - r["start"] < INFRA_QUICK
    except (KeyError, TypeError):
        return False
    if not quick or r["reason"] or r["exit"] == 0:
        return False
    if r.get("is_error") is None:  # no result line: its log is all there is to go by
        return r.get("spoke") is False
    return bool(r.get("is_error")) and not r.get("usd")


def _model_unreachable(c: Ctx, job: str, kind: str) -> None:
    """Nothing was learned about the job: no tier, attempt or review is spent. It waits and tries again."""
    j = c.st["jobs"][job]
    j["status"] = WAITING[kind]
    if kind == "worker":
        j["setup_done"] = True  # no reason on the receipt: setup got through
        j["attempts"] = max(0, j["attempts"] - 1)
        j["session"] = None  # the same death as a session that cannot be resumed: the next try starts fresh
    n = _wait_longer(j, "infra_tries")
    if n >= INFRA_LIMIT:
        return block(c, job, f"the model stayed unreachable for {n} tries")
    if n == 2:  # the first may have been a lost session, which the fresh start above heals
        run.alert(c.st, f"{job}: the model is unreachable (offline or auth expired?): nothing is charged, "
                        "it keeps retrying with a growing wait")
    run.event(c.st, f"{job}: its {kind} got no answer from the model; try {n}")


def _save_now(st: dict) -> bool:
    """Progress on disk before the next step pays for anything. Whether it got there: if not, nothing new
    starts in this cycle; the save at the end of the cycle tries again, and that one tells Marco."""
    try:
        run.save(st)
        return True
    except Exception:
        return False


def consume_receipts(c: Ctx) -> bool:
    """Whether all it learned is on disk."""
    saved = True
    for name in proc.finished(c.dir):
        try:
            r = proc.consume(c.dir, name)
        except OSError as e:
            _cannot_archive(c, name, e)
            continue
        if r is None:
            continue
        j = c.st["jobs"].get(name.rpartition(".")[0])
        if j is not None:
            j.pop("error", None)
            j.pop("errors", None)
        elif name == DECISIONS:
            _forget_retries(c.st)
        _on_receipt(c, name, r)
        if _save_now(c.st):  # the receipt is gone: a crash now must not run this process again
            proc.forget_stop(c.dir, name)
        else:
            saved = False
    return saved


def _cannot_archive(c: Ctx, name: str, e: OSError) -> None:
    """Something sits where a result must be archived (a file at done/, a directory at <name>.proc). It stays one
    process's trouble: the rest of the run moves on, it retries in case he removes it, and then the job blocks. A
    process that nothing waits for any more is forgotten, or the run could never close."""
    if name == DECISIONS:
        return _closing_failed(c, e)
    job = name.rpartition(".")[0]
    j = c.st["jobs"].get(job)
    if j is not None and j["status"] not in run.TERMINAL:
        _error(c, job, e)
    if (j is None or j["status"] in run.TERMINAL) and proc.discard(c.dir, name):
        run.event(c.st, f"forgot {_clean(name)}, its result could not be archived; if it was still running, "
                        "it was not signalled")


def _on_receipt(c: Ctx, name: str, r: dict) -> None:
    if name == DECISIONS:
        if c.st["closing"] == "decisions":
            try:
                on_decisions(c, r)
            except Exception as e:  # the receipt is gone and the tick is paid: publish takes it from here
                run.event(c.st, f"the decisions tick's answer could not be kept: {type(e).__name__}: {_clean(str(e))}")
                c.st["closing"] = "publish"
        else:
            run.event(c.st, f"ignored the result of {name}: nothing was waiting for it")
        return
    job, _, kind = name.rpartition(".")
    j = c.st["jobs"].get(job)
    if j is not None:  # spent and alive whether or not anything still waits for the answer
        j["usd"] = round(j["usd"] + _usd(r.get("usd")), 4)
    if r.get("survivors"):
        run.alert(c.st, f"{_clean(job)}: processes of its {_clean(kind)} outlived it ({r['reason'] or 'it ended'}): "
                        "look for leftovers before they touch its worktree")
    if j is not None and r["reason"] == proc.STOPPED_BY_MARCO and j["status"] not in run.TERMINAL:
        block(c, job, proc.STOPPED_BY_MARCO)  # before the check below: whatever the job waits for, it ends here
        return
    if j is None or kind not in HANDLERS or RUNNING.get(j["status"]) != kind:
        run.event(c.st, f"ignored the result of {_clean(name)}: nothing was waiting for it")
        return
    if kind in MODEL_KINDS:
        if _learned_nothing(r):
            _model_unreachable(c, job, kind)
            return
        j.pop("infra_tries", None)
        j.pop("not_before", None)
    try:
        HANDLERS[kind](c, job, r)
    except Exception as e:  # the receipt is gone: never leave the job waiting for it
        fail(c, job, f"runner error after {kind}: {type(e).__name__}: {e}")


# ---- starting processes -----------------------------------------------------------------------

def _worktree_free(c: Ctx, job: str) -> bool:
    """A worker's Bash shells leave its process group, so a retry could run beside the last attempt's
    leftovers. Not knowing is treated like knowing there are some."""
    j, wt = c.st["jobs"][job], c.wt(job)
    try:
        pids = proc.cwd_holders(wt)
        what = f"processes {pids} are still in its worktree {wt}"
    except Exception as e:
        pids, what = None, f"cannot tell who is in its worktree {wt}: {e}"
    if pids == []:
        j.pop("holders_since", None)
        return True
    if "holders_since" not in j:
        j["holders_since"] = time.time()
        run.alert(c.st, f"{job} waits: {what}")
    elif time.time() - j["holders_since"] > HOLDERS_PATIENCE:
        block(c, job, f"{what} (waited {HOLDERS_PATIENCE // 60} minutes)")
    return False


def start_worker(c: Ctx, job: str) -> bool:
    """Whether a worker was started: a job whose worktree is held stays where it is."""
    if not _worktree_free(c, job):
        return False
    j, mj, wt = c.st["jobs"][job], c.mjobs[job], c.wt(job)
    fresh = not wt.exists()
    if fresh:
        gitops.fetch(c.repo)
    gitops.add_worktree(c.repo, wt, c.branch(job), f"origin/{c.base}")  # refuses a path left on another branch
    if fresh:
        j["base"] = gitops.rev(wt, "HEAD")
    pre = [] if j["setup_done"] else list(c.plan.manifest.get("setup") or [])
    (c.dir / f"{job}.doneblocks").unlink(missing_ok=True)
    report = c.report(job)
    resume = bool(j["session"] and j["resume_msg"])
    if resume:
        prompt = j["resume_msg"]
    else:
        j["session"] = None  # known again only when this process says what it had: one may never start
        prompt = brief.worker_brief(c.st, job, mj, c.plan, worktree=wt, report=report,
                                    note=j["note"] or j["resume_msg"], previous_report=c.read_report(job))
    env = claude.runner_env("worker", origin_url=gitops.origin_url(c.repo), worktree=wt, area=json.dumps(mj["area"]),
                            report=report, gate=mj["gate"], run_dir=c.dir, job=job)
    cmd = claude.agent_cmd("worker", claude.MODELS[j["tier"]], prompt, session=j["session"] or str(uuid.uuid4()), resume=resume,
                           usd=mj["budget"]["usd"])
    proc.start(c.dir, f"{job}.worker", cmd, cwd=wt, env=env, minutes=claude.TIER_MINUTES[j["tier"]], pre=pre)
    j.update(status="working", attempts=j["attempts"] + 1, error=None)
    run.event(c.st, f"{job}: worker {j['tier']} attempt {j['attempts']}{' (resumed)' if resume else ''}")
    return True


def push_job(c: Ctx, job: str) -> None:
    """The worker's result is saved and paid for, so a failed push retries through _error. Only a branch
    on origin that this head does not descend from can never heal: Marco decides what to do with it."""
    j, branch = c.st["jobs"][job], c.branch(job)
    try:
        gitops.push_ref(c.repo, j["head"], branch)
    except gitops.GitError:
        remote = gitops.remote_sha(c.repo, branch)
        if remote and remote != j["head"] and not gitops.is_ancestor(c.repo, remote, j["head"]):
            return block(c, job, f"non-fast-forward: {branch} on origin holds {remote[:8]}, which this head does "
                                 "not descend from: the worker amended or rebased, or someone else pushed there. "
                                 "Delete that branch or look at it")
        raise
    j["status"] = "needs_gate"
    run.event(c.st, f"{job}: head {j['head'][:8]} pushed")


def start_gate(c: Ctx, job: str) -> None:
    j, mj = c.st["jobs"][job], c.mjobs[job]
    env = claude.runner_env("gate", origin_url=gitops.origin_url(c.repo))  # it runs tests a worker wrote
    proc.start(c.dir, f"{job}.gate", ["sh", "-c", mj["gate"]], cwd=c.wt(job), env=env,
               minutes=mj["budget"]["minutes"], meta={"head": j["head"]})
    j["status"] = "gating"


def ensure_pr(c: Ctx, job: str) -> None:
    j, mj = c.st["jobs"][job], c.mjobs[job]
    if not j["pr"]:
        j["pr"] = c.forge.create(c.repo, base=c.base, head=c.branch(job), title=mj["pr_title"],
                                 body=brief.pr_body(c.st, job, mj, c.read_report(job)))
        run.event(c.st, f"{job}: PR {j['pr']['url']}")
    j["status"] = "needs_review"


def start_review(c: Ctx, job: str) -> None:
    j, mj = c.st["jobs"][job], c.mjobs[job]
    tier = "T2" if j["tier"] == "T2" else "T1"
    prompt = brief.reviewer_brief(c.st, job, mj, c.plan, base=f"origin/{c.base}", head=j["head"])
    cmd = claude.agent_cmd("reviewer", claude.MODELS[tier], prompt, session=str(uuid.uuid4()),
                           usd=max(1, mj["budget"]["usd"] / 2))
    env = claude.runner_env("reviewer", origin_url=gitops.origin_url(c.repo), worktree=c.wt(job),
                            area=json.dumps(mj["area"]), job=job, run_dir=c.dir)
    proc.start(c.dir, f"{job}.review", cmd, cwd=c.wt(job), env=env, minutes=30, meta={"head": j["head"]})
    j["status"] = "reviewing"


def start_integrate(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    if j["pr"] and merge.confirmed(c.st, job, c.forge, c.repo):  # a crash lost it: a red check now would pay a worker
        return _merged(c, job)
    cmd = [str(paths.code_dir() / "bin" / "ark-integrate"), f"{c.st['repo']}/{c.st['slug']}", job, j["head"]]
    env = claude.runner_env("integrate", origin_url=gitops.origin_url(c.repo))  # it runs tests a worker wrote
    # Above the check's own timeout, or a slow gate dies here with no result instead of red with its reason.
    proc.start(c.dir, f"{job}.integrate", cmd, cwd=paths.code_dir(), env=env,
               minutes=integrate.GATE_TIMEOUT / 60 + 30, meta={"head": j["head"]})
    j["status"] = "integrating"


def _merged(c: Ctx, job: str) -> None:
    c.st["jobs"][job]["status"] = "merged"
    run.event(c.st, f"{job}: merged into ark/{c.st['slug']}")
    try:
        gitops.remove_worktree(c.repo, c.wt(job))
    except gitops.GitError as e:  # the job is merged all the same
        run.event(c.st, f"{job}: its worktree stays: {e}")


def do_merge(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    if merge.confirmed(c.st, job, c.forge, c.repo):  # a merge whose result was never saved moved the base too
        return _merged(c, job)
    gitops.fetch(c.repo)
    rec = merge.read_record(c.dir, job, "integrate")
    if not rec or rec.get("base") != gitops.rev(c.repo, f"origin/{c.base}"):
        j["status"] = "needs_integrate"
        run.event(c.st, f"{job}: ark/{c.st['slug']} moved since its integration gate; checking again")
        return
    try:
        merge.merge(c.st, job, c.forge, c.repo)
    except merge.MergeRefused as e:
        return block(c, job, f"ark-merge refused: {e}")
    _merged(c, job)


def start_judge(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    allowed = allowed_actions(j)
    if allowed == ["block"]:
        return block(c, job, f"{j['failure']} (no retry or escalation left)")
    prompt = brief.judge_prompt(c.st, job, j, c.mjobs[job], allowed, c.read_report(job))
    cmd = claude.agent_cmd("tick", claude.TICK_MODEL["judge"], prompt, session=str(uuid.uuid4()), usd=1)
    proc.start(c.dir, f"{job}.judge", cmd, cwd=ticks_dir(), env=claude.runner_env("tick"), minutes=15)
    j["status"] = "judging"


# ---- closing ----------------------------------------------------------------------------------

STEP_ERRORS = (gitops.GitError, forge_mod.ForgeError)  # offline, a refused push: healed by trying again


def _reports(c: Ctx) -> dict[str, str]:
    return {jid: c.read_report(jid) or "" for jid in c.st["jobs"]}


def maybe_close(c: Ctx) -> None:
    st = c.st
    if any(j["status"] not in run.TERMINAL for j in st["jobs"].values()) or proc.active(c.dir):
        return
    if st["closing"] is None:
        start_decisions(c)
    elif st["closing"] == "publish" and st.get("closing_wait", 0) <= time.time():
        publish(c)


def start_decisions(c: Ctx) -> None:
    """The tick is paid, so its count and "decisions" reach disk first: a crash after the start finds its
    process, and one before it is one of the DECISIONS_TICKS. With none left, or if it cannot start, publish
    has no answer to find and writes the fallback."""
    st = c.st
    if st.get("decisions_ticks", 0) >= DECISIONS_TICKS:
        st["closing"] = "publish"
        return run.event(st, f"the decisions tick was lost {DECISIONS_TICKS} times: no other is started")
    st["decisions_ticks"] = st.get("decisions_ticks", 0) + 1
    st["closing"] = "decisions"
    run.event(st, "every job is merged or blocked: writing the decisions file")
    if not _save_now(st):
        return
    try:
        cmd = claude.agent_cmd("tick", claude.TICK_MODEL["decisions"], brief.decisions_prompt(st, _reports(c)),
                               session=str(uuid.uuid4()), usd=1)
        proc.start(c.dir, DECISIONS, cmd, cwd=ticks_dir(), env=claude.runner_env("tick"), minutes=15)
    except Exception as e:
        st["closing"] = "publish"
        run.event(st, f"the decisions tick could not start: {type(e).__name__}: {_clean(str(e))}")


def _write_decisions(c: Ctx, text: str) -> str:
    """A new file put in place, never a write through what is there: a worker can plant a symlink in the run
    folder, and a fixed temporary name could be one too."""
    text = _clean(text).rstrip() + "\n"
    fd, tmp = tempfile.mkstemp(dir=c.dir, prefix=".decisions.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, c.dir / "decisions.md")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return text


def on_decisions(c: Ctx, r: dict) -> None:
    text = _clean(r["result"] or "").strip() if r["exit"] == 0 and not r["reason"] else ""
    if not text.startswith("# Decisions"):
        text = brief.fallback_decisions(c.st, _reports(c))
        run.event(c.st, "the decisions tick gave no file: the runner wrote the fallback")
    _write_decisions(c, text)
    c.st["closing"] = "publish"


def _decisions_text(c: Ctx) -> str:
    """What to commit: the file on_decisions left, or the fallback if it is not there or is not one any more."""
    text = _read_untrusted(c.dir / "decisions.md")
    if text and text.startswith("# Decisions"):
        return text
    text = _write_decisions(c, brief.fallback_decisions(c.st, _reports(c)))
    run.event(c.st, "there is no decisions file from the tick: the runner wrote the fallback")
    return text


def _forget_retries(st: dict) -> None:
    for key in ("closing_error", "closing_errors", "closing_wait"):
        st.pop(key, None)


def _stop_closing(c: Ctx, why: str) -> None:
    """A finished run is never entered again, so this tells Marco what is left to do by hand."""
    st = c.st
    _forget_retries(st)
    st.update(status="blocked", closing="done")
    file = f"The decisions file is on ark/{st['slug']}" if st.get("decisions_sha") else \
        f"The decisions file was not committed; its text, if it could be written, is at {c.dir / 'decisions.md'}"
    run.alert(st, f"run blocked: {why}. {file}, and no final PR was opened: the rest is by hand")


def _closing_failed(c: Ctx, e: Exception) -> None:
    """Like _error for a job: a git / gh error may heal (a laptop offline), so it is retried, every cycle until
    Marco hears about it and then at the ceiling, up to the limit. An error not known to heal stops at the alert."""
    st, text = c.st, _clean(f"{type(e).__name__}: {e}")
    n = st["closing_errors"] = st.get("closing_errors", 0) + 1
    if st.get("closing_error") != text:  # not "error": a clean step would wipe it and repeat this event
        st["closing_error"] = text
        run.event(st, f"closing: {text} (retrying)")
    if n >= (INFRA_LIMIT if isinstance(e, STEP_ERRORS) else ERROR_ALERT_AFTER):
        return _stop_closing(c, f"closing failed {n} times: {text}")
    if n == ERROR_ALERT_AFTER:
        run.alert(st, f"closing has been failing for {n} cycles, still retrying, and it stops after {INFRA_LIMIT} "
                      f"tries: {text}")
    if n >= ERROR_ALERT_AFTER:
        st["closing_wait"] = time.time() + BACKOFF_MAX


def publish(c: Ctx) -> None:
    """Commit docs/decisions/<slug>.md on ark/<slug>, open the final PR, finish. Retried within _closing_failed."""
    st = c.st
    try:
        if not st.get("decisions_sha"):
            st["decisions_sha"] = gitops.commit_file(
                c.repo, c.base, f"docs/decisions/{st['slug']}.md", _decisions_text(c),
                f"docs: decisions for {st['slug']}", paths.worktrees_dir() / st["repo"] / st["slug"] / "_decisions")
            run.event(st, f"decisions file committed on ark/{st['slug']}")
        merged = any(j["status"] == "merged" for j in st["jobs"].values())
        if merged and not st["final_pr"]:
            st["final_pr"] = c.forge.create(c.repo, base=st["default_branch"], head=c.base,
                                            title=c.plan.manifest["final_pr_title"], body=brief.final_pr_body(st))
    except gitops.PathRefused as e:  # the branch itself is the problem: no retry heals it
        return _stop_closing(c, _clean(f"{type(e).__name__}: {e}"))
    except Exception as e:
        return _closing_failed(c, e)
    _forget_retries(st)
    all_merged = all(j["status"] == "merged" for j in st["jobs"].values())
    st.update(status="done" if all_merged else "blocked", closing="done")
    pr = f"final PR {st['final_pr']['url']}" if st["final_pr"] else "no job merged, no final PR"
    run.alert(st, f"run {st['status']}: {pr}")


# ---- scheduling -------------------------------------------------------------------------------

class Slots:
    """Writers (workers alive) per repo and in total, and heavy gates, across every run."""

    def __init__(self, states, limits: dict):
        self.limits, self.writers, self.heavy = limits, {}, 0
        for st in states:
            for j in st["jobs"].values():
                if j["status"] == "working":
                    self.writers[st["repo"]] = self.writers.get(st["repo"], 0) + 1
                if j.get("heavy") and j["status"] in ("gating", "integrating"):
                    self.heavy += 1

    def writer(self, repo: str) -> bool:
        if self.writers.get(repo, 0) >= self.limits["writers_per_repo"] or \
                sum(self.writers.values()) >= self.limits["writers_total"]:
            return False
        self.writers[repo] = self.writers.get(repo, 0) + 1
        return True

    def release_writer(self, repo: str) -> None:
        self.writers[repo] -= 1

    def gate(self, heavy: bool) -> bool:
        if not heavy:
            return True
        if self.heavy >= self.limits["heavy_gates"]:
            return False
        self.heavy += 1
        return True


def _start_writer(c: Ctx, job: str, slots: Slots) -> None:
    started = False
    try:
        started = start_worker(c, job)
    finally:
        if not started:  # a job that cannot start must not starve the others
            slots.release_writer(c.st["repo"])


def _next_step(c: Ctx, job: str, j: dict, slots: Slots):
    """What moves this job now, or None while it has to wait. Asking takes the slot the step needs."""
    s, repo = j["status"], c.st["repo"]
    if j.get("not_before", 0) > time.time():
        return None
    if s in ("pending", "needs_merge"):  # needs_merge: a new approval may have added the dependency
        deps = [c.st["jobs"][d]["status"] for d in c.mjobs[job].get("after") or []]
        if "blocked" in deps:
            return lambda: block(c, job, "a job it depends on is blocked")
        if not all(d == "merged" for d in deps):
            return None
    if s in ("pending", "needs_worker"):
        if slots.writer(repo):
            return lambda: _start_writer(c, job, slots)
    elif s == "needs_gate":
        if slots.gate(j["heavy"]):
            return lambda: start_gate(c, job)
    elif s == "needs_integrate":
        if slots.gate(j["heavy"]):
            return lambda: start_integrate(c, job)
    else:
        step_ = {"needs_push": push_job, "needs_pr": ensure_pr, "needs_review": start_review, "needs_merge": do_merge,
                 "needs_judge": start_judge}.get(s)
        if step_:
            return lambda: step_(c, job)
    return None


def schedule(c: Ctx, slots: Slots) -> None:
    for job, j in c.st["jobs"].items():
        move = _next_step(c, job, j, slots)
        if move is None:
            continue
        try:
            move()
        except Exception as e:  # one job's trouble must not stop the jobs after it
            _error(c, job, e)
        else:
            j.pop("error", None)
            j.pop("errors", None)


def reconcile(st: dict) -> None:
    """The state is saved at the end of a cycle and processes start and end during it, so a crash can
    leave them apart. A process the state does not know about is adopted, never started twice; a status
    waiting for a process that is gone goes back a step, or the job would wait forever."""
    if st["status"] in ("done", "blocked"):
        return
    d = paths.run_dir(st["repo"], st["slug"])
    if st.get("closing") == "decisions" and not (d / f"{DECISIONS}.proc").exists():
        st["closing"] = None
        run.event(st, "the decisions tick is gone without a result; starting it again")
    for job, j in st["jobs"].items():
        s = j["status"]
        if s in RUNNING:
            kind = RUNNING[s]
            if not (d / f"{job}.{kind}.proc").exists():
                if proc.stop_requested(d, f"{job}.{kind}"):  # consumed, then a crash before the save
                    _block(st, job, proc.STOPPED_BY_MARCO)
                    continue
                j["status"] = WAITING[kind]
                run.event(st, f"{job}: its {kind} is gone without a result; going back to {j['status']}")
            continue
        kinds = ["worker"] if s == "pending" else [k for k, w in WAITING.items() if w == s]
        for kind in kinds:
            if (d / f"{job}.{kind}.proc").exists():
                j["status"] = next(r for r, k in RUNNING.items() if k == kind)
                if kind == "worker":
                    j["attempts"] += 1
                run.event(st, f"{job}: found its {kind} running; the state had not recorded it")


def step(st: dict, states: list[dict], forge, slots: Slots) -> None:
    if st["status"] in ("done", "blocked"):
        return
    if st["status"] == "waiting":
        if not try_start(st, states):
            return
        if not _save_now(st):  # the jobs exist now: a crash below finds its worker by them, not by chance
            return
    proc.reap(paths.run_dir(st["repo"], st["slug"]))  # first: nothing below can excuse a process from its deadline
    if merge.plan_reason(st):  # the same question ark-merge asks: a run it would refuse takes no step
        if st["status"] != "held":
            st["status"] = "held"
            run.alert(st, "the plan changed after its approval: nothing new is dispatched. Running processes "
                          "finish; their results wait. Restore the approved text, or approve the new text: then "
                          "every job not merged passes its checks again.")
        return
    if st["status"] == "held":
        st["status"] = "running"
        st.pop("reapprove_refused", None)
        run.event(st, "the approved plan is back: running again")
    c = Ctx(st, forge)
    if consume_receipts(c):
        schedule(c, slots)
        maybe_close(c)


def _adopt_reapproval(st: dict, a: dict, states: list[dict], forge) -> None:
    """Marco edited a held run's plan and approved it again: that releases it, if the new plan still
    describes this run. The old text is gone, so nobody can say what the edit changed: every job not merged
    earns its checks again under the plan approved now, and a merged job, which cannot, refuses the approval.
    A plan with other jobs, or one running into another run's area, is a new run. A refusal leaves it held
    and he hears why once."""
    if a["sha256"] == st["plan_sha"]:
        return
    if st["status"] in ("done", "blocked"):  # only a plan with no run file runs: he must not wait for this one
        if st.get("reapprove_refused") != a["sha256"]:
            st["reapprove_refused"] = a["sha256"]
            run.alert(st, f"this run already finished ({st['status']}): the new approval starts nothing. "
                          "To run the new plan, approve it under another slug")
        return
    if st["status"] != "held":
        return
    plan = Path(a["plan"])
    if not plan.is_file() or sha256_file(plan) != a["sha256"]:  # edited since: not what was approved
        return
    why, area, jobs = "", [], []
    problems = validate(plan, approved=True)
    if problems:
        why = f"it does not validate: {problems[0].message}"
    else:
        jobs = load_plan(plan).manifest["jobs"]
        area = [g for j in jobs for g in j["area"]]
        merged = [job for job, j in st["jobs"].items() if j["status"] == "merged"]
        if {j["id"] for j in jobs} != set(st["jobs"]):
            why = "it changes the run's jobs"
        elif merged:
            why = f"{', '.join(merged)} already merged under the old plan and cannot pass the new one's checks"
        for o in states:
            if not why and o is not st and o["repo"] == st["repo"] and o["status"] in ("running", "held") \
                    and areas_overlap(area, o.get("area") or []):
                why = f"its area overlaps the running plan {o['slug']}"
    if why:
        if st.get("reapprove_refused") != a["sha256"]:
            st["reapprove_refused"] = a["sha256"]
            run.alert(st, f"it cannot take the new approval: {why}. It stays held. To go on with this run, restore "
                          "the approved text and approve it again; for a different run, approve under another "
                          "slug (this one keeps its area until it ends)")
        return
    d = paths.run_dir(st["repo"], st["slug"])
    done = proc.finished(d)
    if any(n.rpartition(".")[2] in CHECK_KINDS and n not in done for n in proc.active(d)):
        return  # a check under the old plan still runs. It has a time limit, so this retries soon
    consume_receipts(Ctx(st, forge))  # a stop from Marco or a block still counts; a green no longer does
    for mj in jobs:
        j = st["jobs"][mj["id"]]
        if j["status"] == "pending":
            st["jobs"][mj["id"]] = run.new_job(mj)  # nothing done yet: all it copied comes from the new plan
            continue
        j["heavy"] = bool(mj.get("heavy"))
        if j["status"] in REWOUND:
            j["status"] = "needs_gate"
        merge.drop_records(d, mj["id"])  # before the new sha can reach disk: no old green under a new plan
    st.pop("reapprove_refused", None)
    st.update(plan=a["plan"], plan_sha=a["sha256"], approved_at=a["approved_at"], area=area)
    run.event(st, f"approved again ({a['sha256'][:8]}): the run takes the new plan, and every job not merged "
                  "passes its checks again")


def status_line(states: list[dict]) -> str:
    live = [s for s in states if s["status"] not in ("done", "blocked")]
    finished = len(states) - len(live)
    parts = []
    for s in live:
        n, m = len(s["jobs"]), sum(j["status"] == "merged" for j in s["jobs"].values())
        parts.append(f"{s['repo']}/{s['slug']} {s['status']}" + (f" {m}/{n} merged" if n else ""))
    line = " · ".join(parts) or "idle"
    return line + (f" · {finished} finished" if finished else "")


def _load(repo: str, slug: str) -> dict | None:
    """run.load, plus the shape the loop relies on: valid JSON of the wrong shape is damage too."""
    st = run.load(repo, slug)
    if st is None:
        return None
    jobs = st.get("jobs")
    if (merge.state_reason(st) or (st["repo"], st["slug"]) != (repo, slug) or not isinstance(st.get("status"), str)
            or not isinstance(st.get("events"), list) or not isinstance(st.get("alerts"), list)
            or not all(isinstance(j, dict) and isinstance(j.get("status"), str) for j in jobs.values())):
        raise run.StateCorrupt(f"malformed run state {run.state_file(repo, slug)}")
    return st


def _once(key: tuple, text: str, alerts: list[str]) -> None:
    key = (str(paths.ark_home()), *key)
    if key not in _said:
        _said.add(key)
        alerts.append(text)


def _guarded(st: dict, key: str, fn) -> None:
    """One broken run must not stop the others; Marco hears about each new error once."""
    try:
        fn()
        st.pop(key, None)
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
        if st.get(key) != text:
            st[key] = text
            run.alert(st, f"runner error: {text}")


def _orphans(known, alerts: list[str]) -> None:
    """A live run with no valid approval takes no step, but Marco hears about it and what it left
    running keeps its deadlines. Only a state that says it finished is taken for finished."""
    root = paths.runs_dir()
    for f in root.glob("**/state.json"):
        rel = f.parent.relative_to(root)
        key = (str(rel.parent), rel.name)
        if key in known:
            _said.discard((str(paths.ark_home()), "no_approval", *key))
            continue
        try:
            st = _load(*key)
            if st and st["status"] in ("done", "blocked"):
                continue
        except Exception:
            pass
        _once(("no_approval", *key), f"{key[0]}/{key[1]}: this run has no valid approval: it takes no new step "
                                     "until the approval is back. Its processes keep their time limits.", alerts)
        try:
            proc.reap(paths.run_dir(*key))
        except Exception as e:
            _once(("no_approval_reap", *key), f"{key[0]}/{key[1]}: cannot check its processes: "
                                              f"{type(e).__name__}: {e}", alerts)


def cycle(forge=None) -> tuple[str, list[str], bool]:
    """One pass. Returns (status line, alerts to show, whether any run state changed)."""
    forge = forge or forge_mod.get()
    limits = paths.load_workspace()["limits"]  # first: if it raises, no alert was consumed and lost
    states, before, alerts, approved = {}, {}, [], {}
    for a in approvals():
        key = (a["repo"], a["slug"])
        approved[key] = a
        try:
            st = _load(*key)
        except Exception as e:  # never new_state over it: only Marco can say what it should have been
            _once(("unreadable", *key), f"{key[0]}/{key[1]}: the run is skipped until its state is repaired: "
                                        f"{type(e).__name__}: {e}", alerts)
            continue
        _said.discard((str(paths.ark_home()), "unreadable", *key))
        before[key] = json.dumps(st, sort_keys=True) if st else ""
        states[key] = st or run.new_state(a)
        if states[key]["status"] == "waiting":  # nothing started yet: a newer approval simply replaces the older
            states[key].update(plan=a["plan"], plan_sha=a["sha256"], approved_at=a["approved_at"])
    _orphans(approved, alerts)
    for key, st in states.items():
        _guarded(st, "reapprove_error", lambda st=st, a=approved[key]: _adopt_reapproval(st, a, list(states.values()), forge))
    for st in states.values():
        _guarded(st, "reconcile_error", lambda st=st: reconcile(st))
    slots = Slots(states.values(), limits)
    home = str(paths.ark_home())
    for key, st in states.items():
        if (home, "unsaved", *key) in _said:  # moving it on would pay again for work it cannot remember
            _guarded(st, "error", lambda d=paths.run_dir(*key): proc.reap(d))
        else:
            _guarded(st, "error", lambda st=st: step(st, list(states.values()), forge, slots))
    changed = False
    for key, st in states.items():
        alerts += st["alerts"]
        st["alerts"] = []
        dirty = json.dumps(st, sort_keys=True) != before[key]
        changed = changed or dirty
        if dirty or (home, "unsaved", *key) in _said:
            try:
                run.save(st)
                _said.discard((home, "unsaved", *key))
            except Exception as e:  # until a save works this run only reaps; then reconcile() catches up
                _once(("unsaved", *key), f"{key[0]}/{key[1]}: the run state cannot be saved: "
                                         f"{type(e).__name__}: {e}", alerts)
    return status_line(list(states.values())), alerts, changed


# ---- main ---------------------------------------------------------------------------------------

def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except (OSError, ValueError):  # gone, a dangling link, a NUL in a path: approvals() tolerates them too
        return 0


def signature() -> str:
    """Cheap fingerprint of what can wake the loop: approvals, plan files, receipts, processes past
    their deadline or dead without a receipt. A change runs a cycle before the next full tick."""
    h = hashlib.sha256()
    for f in sorted(paths.approvals_dir().glob("**/*.json")):
        h.update(f"{f}:{_mtime(f)}\n".encode())
    for a in approvals():
        p = Path(a["plan"])
        h.update(f"{p}:{_mtime(p)}\n".encode("utf-8", "replace"))
    for f in sorted(paths.runs_dir().glob("**/*.receipt.json")):
        h.update(f"{f}\n".encode())
    for d in sorted({f.parent for f in paths.runs_dir().glob("**/*.proc")}):
        for n in proc.overdue(d):
            h.update(f"{d}/{n}:overdue\n".encode())
        for n in proc.active(d):
            info = proc._read(d / f"{n}.proc")
            if not (d / f"{n}.receipt.json").exists() and not (info and proc._alive(d, n, info["pid"])):
                h.update(f"{d}/{n}:dead\n".encode())
    return h.hexdigest()


def _retry_due() -> bool:
    """What a cycle retries on its own clock, with nothing on disk to change the signature: a step that
    failed, a worktree still held, a wait that is over. Those count in cycles (ERROR_ALERT_AFTER) and
    in seconds (BACKOFF_FIRST), so they cannot wait for the tick."""
    now = time.time()
    for a in approvals():
        try:
            st = _load(a["repo"], a["slug"])
        except Exception:  # the cycle says so, once
            continue
        if not st or st["status"] != "running":
            continue
        if st.get("closing_errors") and st.get("closing_wait", 0) <= now:
            return True
        for j in st["jobs"].values():
            if j["status"] in run.TERMINAL:
                continue
            if j.get("errors"):  # a running job has them only while its result cannot be archived
                return True
            if j["status"] not in RUNNING and ("holders_since" in j or 0 < j.get("not_before", 0) <= now):
                return True
    return False


def _setting(ws: dict, key: str) -> float:
    """A positive number: a sleep of a bad value ends the loop, and of 0 it spins."""
    v = ws[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 86400:
        raise ValueError(f"workspace.json: {key} must be a positive number, not {v!r}")
    return v


def _polling(once: bool) -> int:
    poll_default = paths.DEFAULTS["poll_seconds"]
    last_line, last_sig, last_full, last_error = None, None, 0.0, None
    while True:
        poll = poll_default
        try:
            ws = paths.load_workspace()
            poll, tick = _setting(ws, "poll_seconds"), _setting(ws, "tick_minutes") * 60
            sig = signature()
            if once or sig != last_sig or time.time() - last_full >= tick or _retry_due():
                for _ in range(5):                   # follow-up passes while things move
                    sig = signature()                # before the pass: what lands during it wakes the next poll
                    line, alerts, changed = cycle()
                    for a in alerts:
                        print(f"⚠ {a}", flush=True)
                    if not changed or _retry_due():  # a retry waits for the next poll, not for the next pass
                        break
                last_full, last_sig = time.time(), None if changed else sig  # None: go on at the next poll
                if line != last_line:
                    print(f"{datetime.now():%H:%M} {line}", flush=True)
                    last_line = line
            last_error = None
        except Exception as e:  # a bad edit of workspace.json, a passing failure: the runner stays up
            text = f"{type(e).__name__}: {e}"
            if text != last_error:
                print(f"⚠ el ciclo falló, reintentando en cada poll: {text}", flush=True)
            last_error, last_sig = text, None
            if once:
                return 1
        if once:
            return 0
        time.sleep(poll)


def main(argv=None) -> int:
    # A shell without job control starts `ark-loop &` with SIGINT ignored, and then Python installs no handler
    # for it. It must stop on Ctrl-C however it was launched; what it starts gets the default back at exec.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    argv = sys.argv[1:] if argv is None else argv
    if set(argv) - {"--once"}:  # a typo must not start something that spends money
        print("uso: ark-loop [--once]", file=sys.stderr)
        return 2
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("ark-loop no corre con ANTHROPIC_API_KEY: los workers facturarían por token en vez de usar "
              "tu suscripción. Hacé `unset ANTHROPIC_API_KEY` y volvé a correrlo.", file=sys.stderr)
        return 2
    if os.environ.get("ARK_ROLE"):
        print("ark-loop no corre dentro de un proceso del runner.", file=sys.stderr)
        return 2
    for name, where in (("ARK_HOME", paths.ark_home()), ("ARK_WORKTREES", paths.worktrees_dir())):
        if not where.is_absolute():  # the wrapper changed directory: it would land inside the code
            print(f"ark-loop necesita una ruta absoluta en {name}, no {where}.", file=sys.stderr)
            return 2
    sys.stdout.reconfigure(errors="backslashreplace")  # an alert the terminal cannot show must not raise
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    lock_file = paths.runs_dir() / ".loop.lock"
    try:  # never through a link, never truncated; the kernel releases it on any death
        lock = os.open(lock_file, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as e:
        print(f"ark-loop no puede abrir su lock {lock_file}: {e}", file=sys.stderr)
        return 1
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        print("ark-loop ya está corriendo.", file=sys.stderr)
        return 1
    try:
        return _polling("--once" in argv)
    except KeyboardInterrupt:  # Ctrl-C is how it stops; workers live in their own sessions and go on
        return 130
    finally:
        os.close(lock)


if __name__ == "__main__":
    sys.exit(main())
