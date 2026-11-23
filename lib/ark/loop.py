"""ark-loop: the always-on runner (§4, §5). No model: a state machine over files.

Each cycle: every approval → its run (runs/<repo>/<slug>/state.json); start waiting runs; hold a
run whose plan changed; consume process receipts; move every job forward within the limits.
Claude runs only as workers, reviewers, Euklides and two read-only ticks (intervene, decisions)."""
from __future__ import annotations

import fcntl
import copy
import hashlib
import json
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import accounts, amend, approve, beat, brief, caps, claude, coachqueue, companions, daycap, euklides, forge as forge_mod, gitops, inbox, integrate, merge, notify, paths, progress, proc, questions, reporting, reserved, run, sweep, usage, versions
from .globs import areas_overlap, match_any
from .plan import load_plan, sha256_file
from .validate import validate

ERROR_ALERT_AFTER = 20    # cycles (about 5 minutes) a step keeps failing before Marco hears about it
INFRA_QUICK = 120         # seconds: a model process that dies sooner, with no answer, never got to work
INFRA_LIMIT = 200         # tries before a job stops waiting for the model; about 50 hours at the ceiling
BACKOFF_FIRST = 15        # seconds before the first retry; it doubles each time
BACKOFF_MAX = 900         # seconds, the ceiling of that wait
REPORT_MAX = 1_000_000    # bytes of a worker's report the runner reads
DECISIONS_TICKS = 2       # paid ticks a run's close may start before the runner writes the fallback itself
SLEEP_MIN = 60            # seconds the wall clock must run ahead of the uptime clock to count as a sleep
NET_WAIT_MAX = 600        # seconds the loop waits for the network after waking before it goes on
NET_HOSTS = (("api.github.com", 443), ("api.anthropic.com", 443))
CLOCK_KEYS = ("soft_deadline", "watch_from", "not_before")  # absolute times that limit a job
PASSING_WAITS = (60, 120, 240, 480, 900)  # about half an hour in all, then Euklides
REMIND_EVERY = 86400  # an open question is repeated to Marco once a day
WAITS_ON = ("blocked", "waiting_dep", "waiting_answer", "attached", "waiting_fix")  # a dependency in one of these holds its dependents
SMALL_FILES, SMALL_LINES = 3, 30  # the small-fix rule: what Euklides may widen or fix without Marco
FIX_BASE_USD = 2  # the budget of one base-fix worker
REFRESH_PREFIX = "_refresh-"   # a run-level name, like _fix-
REFRESH_EVERY = 60             # seconds between two looks at origin's default branch, per run
REFRESH_RUNNING = {"checking": "check", "working": "worker", "gating": "gate"}
REFRESH_WAITING = {"check": "needs_check", "worker": "needs_worker", "gate": "needs_gate"}
START_CHECK = True             # False: a new run counts its base as green and dispatches at once (tests)

# What a job's status says is running, and where the job goes back to if that process is not there.
RUNNING = {"working": "worker", "gating": "gate", "reviewing": "review", "integrating": "integrate",
           "euklides": "euklides"}
WAITING = {"worker": "needs_worker", "gate": "needs_gate", "review": "needs_review",
           "integrate": "needs_integrate", "euklides": "needs_euklides"}
WAITS_FOR_MARCO = ("waiting_answer", "attached")  # statuses only Marco can move
STEERED = "steered"
INTERVENED = "stopped by the intervene tick"
ATTACHED = "handed to Marco"
API_HUNG = "api-hung"  # an API failure cut its turn and it stayed alive: it is resumed in its session
OURS = (STEERED, INTERVENED, ATTACHED, API_HUNG)  # we stopped it, knowing which session it was
MAX_STEERS = 2

_net_wait_until = 0.0  # after a wake, nothing new starts before the network is back or this time passes
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


def _wt(st: dict, job: str) -> Path:
    return paths.worktrees_dir() / st["repo"] / st["slug"] / job


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
        return _wt(self.st, job)

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
    idle = ("waiting", *run.FINISHED)
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


def try_start(st: dict, others: list[dict], forge) -> bool:
    """waiting → running: the approved plan still validates, the repo is there, none of its branches is a
    branch of another started run, no running run in the same repo has an overlapping area, no finished run
    in the same repo with an overlapping area has its final PR still open (asked of forge; a PR seen merged
    or closed goes in st["settled_prs"] and is not asked again), ark/<slug> and its job branches do not
    exist yet. Then push ark/<slug>. The only way into "running", and one loop runs
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
    if not reason:  # its own default branch would not hold that run's changes yet: Marco hears it once
        settled = st.setdefault("settled_prs", [])
        for o in others:
            pr = o.get("final_pr")
            if o is st or o["repo"] != st["repo"] or o["status"] not in run.FINISHED or not pr \
                    or pr["url"] in settled or not areas_overlap(area, o.get("area") or []):
                continue
            try:
                state = forge.view(repo, pr["number"])["state"]
            except forge_mod.ForgeError as e:
                reason = f"cannot read the final PR {pr['url']} of the plan {o['slug']}: {_clean(str(e))}"
                break
            if state == "OPEN":
                reason = f"its area overlaps the plan {o['slug']}, whose final PR {pr['url']} is still open"
                break
            settled.append(pr["url"])
    if not reason:
        try:
            gitops.fetch(repo)
            default = gitops.default_branch(repo)
            existing = gitops.remote_sha(repo, f"ark/{st['slug']}")
            if existing and existing != st["base_sha"]:
                reason = f"ark/{st['slug']} already exists on origin: delete it, or approve under another slug"
            for job in () if reason else mjobs:
                if st["jobs"].get(job["id"], {}).get("status") not in (None, "pending"):
                    continue  # merged or blocked under an earlier version: its branch is history
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
    if START_CHECK:
        st["refresh"] = _new_refresh(0, base, None)  # no job before a check is green on the base
    st.update(main_seen=base, base_green=not START_CHECK)
    st.setdefault("refreshes", [])
    for mj in mjobs:  # a run that starts again under a new version keeps the jobs it already has
        if mj["id"] not in st["jobs"]:
            st["jobs"][mj["id"]] = run.new_job(mj)
    _first_version(st)
    run.event(st, f"started: ark/{st['slug']} from {default} @ {base[:8]}")
    return True


# ---- job transitions ---------------------------------------------------------------------------

GREEN_HEADS = 10  # heads a job keeps as passed; older ones are forgotten


def _passed(j: dict, head) -> None:
    """A head that passed a gate or a CLEAN review: a later red on it may be load, not code."""
    if head:
        heads = [h for h in j.get("green_heads") or [] if h != head] + [head]
        j["green_heads"] = heads[-GREEN_HEADS:]


def fail(c: Ctx, job: str, why: str, **extra) -> None:
    j, why = c.st["jobs"][job], _clean(why)
    j.update(failure=why, tier_attempts=j["tier_attempts"] + 1)
    j.pop("auto_resumes", None)  # the next worker is a new attempt
    run.event(c.st, f"{job} failed ({j['tier']}, attempt {j['tier_attempts']} at this tier): {why}")
    red = extra.get("red") or {}
    progress.note(j, red.get("tail") or why, red.get("head") or j.get("head"))
    if progress.stalled(j):
        why = f"{why}\n\nNo progress: {progress.why_stalled(j)}. A raise is not open."
    to_euklides(c, job, "failure", why, **extra)


def _block(st: dict, job: str, why: str, critical: bool = False) -> None:
    why = _clean(why)
    j = st["jobs"][job]
    j.pop("regate", None)  # a job that leaves its gate another way never keeps the machine quiet
    note = j.pop("stop_why", None)  # `ark stop --why`: the sentinel itself stays exact, the note rides after it
    if note and why == proc.STOPPED_BY_MARCO:
        why = f"{why}: {_clean(note)[:300]}"
    j.update(status="blocked", reason=why, blocked_from=j["status"])
    _raise_outcome(j, f"blocked: {why[:200]}")
    run.alert(st, f"{job} blocked: {why}", critical=critical)


def block(c: Ctx, job: str, why: str, critical: bool = False) -> None:
    _block(c.st, job, why, critical=critical)


def _to_euklides(st: dict, job: str, trigger: str, situation: str, kind: str | None = None, **extra) -> None:
    st["jobs"][job].pop("regate", None)  # a job that leaves its gate another way never keeps the machine quiet
    st["jobs"][job].update(status="needs_euklides", euklides={
        "trigger": trigger, "situation": _clean(situation)[:questions.MAX_TEXT], "kind": kind, **extra})


def to_euklides(c: Ctx, job: str, trigger: str, situation: str, kind: str | None = None, **extra) -> None:
    """Euklides decides how the job goes on. `kind` names a block a new attempt may fix: area, review,
    integrate, or step (a passing fault; `back` is the status it returns to)."""
    _to_euklides(c.st, job, trigger, situation, kind, **extra)


def _passing(c: Ctx, job: str, counter: str, why: str, back: str) -> None:
    """A fault that heals by waiting: the job returns to `back` after 1, 2, 4, 8 and 15 minutes. Still failing,
    Euklides decides, with the last error; when he lets it go on, it returns to `back`."""
    j = c.st["jobs"][job]
    n = j[counter] = j.get(counter, 0) + 1
    if n <= len(PASSING_WAITS):
        j["status"] = back
        j["not_before"] = time.time() + PASSING_WAITS[n - 1]
        if n == 1:
            run.event(c.st, f"{job}: {_clean(why)} (retrying in a minute)")
        return
    j.pop(counter, None)
    j.pop("not_before", None)
    to_euklides(c, job, "blocked", f"{why} (still failing after {len(PASSING_WAITS)} retries over about "
                                   "30 minutes)", "step", back=back)


def _error(c: Ctx, job: str, e: Exception) -> None:
    """An error while moving a job. Between processes it is a passing fault: the step runs again on the
    PASSING_WAITS schedule, then Euklides decides. A running job's error is an archive failure, counted per cycle."""
    text = _clean(f"{type(e).__name__}: {e}")
    j = c.st["jobs"][job]
    if j["status"] not in RUNNING:
        return _passing(c, job, "step_tries", f"the step failed: {text}", j["status"])
    j["errors"] = j.get("errors", 0) + 1
    if j.get("error") != text:
        j["error"] = text
        run.event(c.st, f"{job}: {text} (retrying)")
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


def _after_stop(st: dict, job: str, then: dict) -> None:
    """What the intervene tick decided, once its worker is gone."""
    j = st["jobs"][job]
    if then["action"] == "stop_escalate" and j["tier"] in claude.NEXT_TIER:
        _escalate(j)
        j.update(note=then["note"], status="needs_worker")
    else:
        _to_euklides(st, job, "blocked", f"the intervene tick stopped it: {then['note']}", None)


def _resume_api_hung(st: dict, job: str, why: str) -> None:
    """A worker we stopped because an API failure left it hung goes on in its own session; the attempt it was in
    is not charged again. A resume while the API is still down dies at once and takes the uncharged backoff."""
    j = st["jobs"][job]
    text = f"What the runner saw: {why}"
    if j["session"]:
        j["resume_msg"] = brief.resume_message("api", text, st["slug"])
    else:
        j["note"] = f"{brief.RESUME['api']}\n\n{text}"
    j["attempts"] = max(0, j["attempts"] - 1)
    j["status"] = "needs_worker"
    limit = paths.load_workspace()["limits"]["auto_resumes"]
    run.event(st, f"{job}: resumed after an API failure ({j.get('auto_resumes', 0)}/{limit}): {why}")


def _stop_api_hung(c: Ctx, job: str, why: str) -> None:
    """Stop a worker whose turn an API failure cut, to resume it in its session (see _resume_api_hung)."""
    j = c.st["jobs"][job]
    j["auto_resumes"] = j.get("auto_resumes", 0) + 1
    j["api_hung"] = _clean(why)[:usage.LAST_MESSAGE]  # a HANDLING key: nothing else acts on the job meanwhile
    proc.stop(c.dir, f"{job}.worker", API_HUNG)


def _resumes_left(j: dict, limits: dict) -> bool:
    return j.get("auto_resumes", 0) < int(limits["auto_resumes"])


def on_worker(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    steering, then, hung = j.pop("steering", None), j.pop("stop_then", None), j.pop("api_hung", None)
    j.update(resume_msg=None, note=None)  # kept until now: a worker that never ran still needs them
    # only a process that says it had one can be resumed, or one we stopped ourselves
    j["session"] = r.get("session") or ((r.get("meta") or {}).get("session") if r["reason"] in OURS else None)
    if j.pop("attaching", None):  # whatever the worker was doing, the job is Marco's now
        j["status"] = "attached"
        run.event(c.st, f"{job}: handed to Marco")
        return
    if then and r["reason"] == INTERVENED:
        return _after_stop(c.st, job, then)
    if hung and r["reason"] == API_HUNG:
        return _resume_api_hung(c.st, job, hung)
    if steering and r["reason"] == STEERED:
        if j["session"]:
            j["resume_msg"] = brief.resume_message("steer", steering, c.st["slug"])
        else:
            j["note"] = steering
        j["status"] = "needs_worker"
        run.event(c.st, f"{job}: redirected: {steering[:300]}")
        return
    if steering:  # `ark steer` said it was noted, and its stop came after the worker's end
        run.alert(c.st, f"the steer for {job} was not delivered: its worker had already ended")
    if not r["reason"]:  # any reason may mean setup never finished: it runs again
        j["setup_done"] = True
    if r["reason"]:
        return fail(c, job, r["reason"])
    if not _clean_exit(r):
        return fail(c, job, f"the worker ended with an error (exit {r['exit']})")
    status, detail = brief.worker_status(r["result"])
    if status == "BLOCKED":  # not a failure: no attempt is charged, and Euklides looks at it before Marco does
        j["question"] = _clean(detail)[:questions.MAX_TEXT] or "the worker ended BLOCKED without a question"
        run.event(c.st, f"{job}: the worker asks: {j['question'][:300]}")
        return to_euklides(c, job, "question", j["question"])
    if status != "DONE":
        return fail(c, job, f"the worker ended {status}: {detail}")
    bad = done_check(c, job)
    if bad and bad[0] == "outside":
        return to_euklides(c, job, "blocked", bad[1], "area")
    if _followup_settled(c, job, bad):
        return
    if bad:
        return fail(c, job, {"dirty": "the worker ended DONE with uncommitted changes",
                             "empty": "the worker ended DONE without any change"}.get(bad[0],
                                                                                       f"the worker ended DONE, but {bad[1]}"))
    j.update(status="needs_push", head=gitops.rev(c.wt(job), "HEAD"))
    run.event(c.st, f"{job}: worker done, head {j['head'][:8]}")


def _followup_settled(c: Ctx, job: str, bad: tuple[str, str] | None) -> bool:
    """A follow-up (a merged job a new plan version changed) that ends with nothing to add, after a worker's DONE
    or Euklides' `fixed`: its earlier work is already in the base, so it ends merged with no push, gate or PR,
    and its dependents go on. An area that only shrank needs no commit. Any other job with no change still fails."""
    j = c.st["jobs"][job]
    f = j.get("followup")
    if not (bad and bad[0] == "empty" and isinstance(f, dict)):
        return False
    for k in ("question", "not_before", "push_tries", "step_tries", "euklides", "euklides_ask", "failure"):
        j.pop(k, None)
    j.update(status="merged", head=gitops.rev(c.wt(job), "HEAD"))
    _raise_outcome(j, "merged")
    run.event(c.st, f"{job}: follow-up of v{f.get('from_version')} needs no change; its merged work stands")
    try:
        gitops.remove_worktree(c.repo, c.wt(job))
    except gitops.GitError as e:  # the job is settled all the same
        run.event(c.st, f"{job}: its worktree stays: {e}")
    return True


def done_check(c: Ctx, job: str) -> tuple[str, str] | None:
    """What a job's worktree must hold before its head is pushed, after a worker's DONE or Euklides' `fixed`:
    a clean tree, on the job's branch, with changes against the base, all inside the area. None when it does;
    otherwise (kind, why): dirty, branch, empty or outside."""
    wt, branch = c.wt(job), c.branch(job)
    if not gitops.is_clean(wt):
        return "dirty", "the tree has uncommitted changes"
    on = gitops.current_branch(wt)
    if on != branch:
        return "branch", f"the worktree is on {_printable(on) if on else 'a detached HEAD'}, not {branch}"
    files = gitops.changed_files(wt, f"origin/{c.base}", "HEAD")
    if not files:
        return "empty", f"nothing changed against origin/{c.base}"
    outside = [_printable(f) for f in files if not match_any(f, run.area(c.st, job, c.mjobs[job]))]
    if outside:
        outside = _absorb_companions(c, job, outside)
    if outside:
        return "outside", "changed files outside its area: " + ", ".join(outside[:5])
    return None


def on_gate(c: Ctx, job: str, r: dict) -> None:
    ok = r["exit"] == 0 and not r["reason"]
    tail = _clean(r["result"] or "")[-2000:]
    j = c.st["jobs"][job]
    accepted = j.pop("accept_gate", None)  # it applies to one run of the gate: a red one asks again
    extra = {"accepted_gate": accepted} if accepted else {}
    merge.record(c.dir, job, "gate", {"ok": ok, "head": r["meta"].get("head"), "exit": r["exit"], "tail": tail, **extra})
    rg = j.pop("regate", None) if ok else None
    if ok:
        _passed(j, r["meta"].get("head"))
        if rg:
            _flaky(c, job, rg)
        j["status"] = "needs_pr"
        run.event(c.st, f"{job}: job gate green" + (f" (the replacement gate `{_clean(accepted)[:200]}`)" if accepted else ""))
    elif j.get("regate"):
        return _regate_ask(c, job, "the job gate is red again, alone on the machine", tail)
    elif accepted:
        why = (f"the replacement gate `{_clean(accepted)[:200]}` is red on head {str(r['meta'].get('head'))[:8]} "
               f"(exit {r['exit']}{', ' + r['reason'] if r['reason'] else ''})")
        run.event(c.st, f"{job}: {why}")
        _euklides_ask(c.st, job, {"trigger": "blocked", "situation": f"{why}:\n{tail[-1200:]}", "kind": None},
                      {"note": f"Accepting the head on the replacement gate failed: {why}.",
                       "proposal": ":stop, or say what the job should do", "then": "stop"}, None)
    else:
        fail(c, job, f"job gate red (exit {r['exit']}{', ' + r['reason'] if r['reason'] else ''}): {tail[-600:]}",
             red={"kind": "gate", "head": r["meta"].get("head"), "tail": tail})


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
            return to_euklides(c, job, "blocked", "the reviewer gave no verdict twice", "review")
        j["status"] = "needs_review"
        run.event(c.st, f"{job}: the reviewer gave no verdict; reviewing again")
        return
    j["review_errors"] = 0
    verdict, findings = answer[0], [str(f) for f in answer[1]][:20]
    merge.record(c.dir, job, "verdict", {"verdict": verdict, "head": r["meta"].get("head"), "findings": findings})
    if verdict == "CLEAN":
        _passed(j, r["meta"].get("head"))
        j["status"] = "needs_integrate"
        run.event(c.st, f"{job}: review CLEAN")
        return
    j["review_rounds"] += 1
    run.event(c.st, f"{job}: review FIX (round {j['review_rounds']}): {len(findings)} findings")
    if j["review_rounds"] >= 3:
        return fail(c, job, "3 review rounds without CLEAN: " + "; ".join(findings)[:600])
    j["resume_msg"] = brief.resume_message("review", "\n".join(f"- {f}" for f in findings), c.st["slug"])
    j["status"] = "needs_worker"


def on_integrate(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    out = None if r["reason"] else _answer(r)
    if not isinstance(out, dict) or "ok" not in out:  # a stop or a death: nothing was learned about the job
        why = r["reason"] or "ark-integrate gave no result"
        return _passing(c, job, "integrate_lost", f"ark-integrate ended without a result ({why})", "needs_integrate")
    ok = out["ok"] is True and isinstance(out.get("base"), str)
    reason = out.get("reason", "")
    if not ok and out.get("base") is None:  # the check never ran: not the worker's doing
        return _passing(c, job, "integrate_skips", f"the integration check could not run: {reason}",
                        "needs_integrate")
    j.pop("integrate_lost", None)
    j.pop("integrate_skips", None)
    j.pop("not_before", None)
    merge.record(c.dir, job, "integrate", {"ok": ok, "base": out.get("base"),
                                           "head": out.get("head") or r["meta"].get("head"),
                                           "reason": reason, "tail": out.get("tail", "")})
    if ok:
        rg = j.pop("regate", None)
        if rg:
            _flaky(c, job, rg)
        j["status"] = "needs_merge"
        run.event(c.st, f"{job}: integration gate green on {out['base'][:8]}")
        return
    if j.get("regate"):
        return _regate_ask(c, job, "the integration gate is red again, alone on the machine",
                           str(out.get("tail", "")))
    red = {"kind": "integrate", "head": out.get("head") or r["meta"].get("head"),
           "tail": str(out.get("tail", ""))[-2000:]}
    if out.get("base_red") is True:  # not the job's doing: a second worker attempt would only fail the same way
        return to_euklides(c, job, "blocked", f"integration gate red, and red on {c.base} without the job too: "
                           f"{reason}\n\n{str(out.get('tail', ''))[-1500:]}", "integrate", base_red=True, red=red)
    j["integrate_fails"] += 1
    if j["integrate_fails"] >= 2:
        return to_euklides(c, job, "blocked", f"integration gate failed twice: {reason}\n\n"
                           f"{str(out.get('tail', ''))[-1500:]}", "integrate", red=red)
    j["resume_msg"] = brief.resume_message("integrate", f"{reason}\n\n{out.get('tail', '')}", c.st["slug"])
    j["status"] = "needs_worker"
    run.event(c.st, f"{job}: integration gate red ({reason}); back to the worker")


def _tick_answer(r: dict) -> tuple[object, str]:
    """(action, note) from an intervene tick's answer of exactly the agreed shape; (None, "") for anything else."""
    d = _answer(r) if _clean_exit(r) else None
    if not isinstance(d, dict) or set(d) != {"action", "note"} or not isinstance(d["note"], str):
        return None, ""
    return d["action"], d["note"].strip()[:1000]


def _resume_with_answer(st: dict, job: str, answer: str) -> None:
    j = st["jobs"][job]
    text = f"Question: {j.pop('question', None)}\n\nAnswer: {answer}"
    if j["session"]:
        j["resume_msg"] = brief.resume_message("answer", text, st["slug"])
    else:
        j["note"] = text
    j["status"] = "needs_worker"


def _escalate(j: dict) -> None:
    j["tier"] = claude.NEXT_TIER[j["tier"]]
    j["tier_path"].append(j["tier"])
    j.update(tier_attempts=0, session=None, resume_msg=None, review_rounds=0)
    j.pop("auto_resumes", None)


NO_ANSWER = {"action": "ask", "note": "Euklides gave no usable answer", "undo": None, "why": "", "proposal": None}


def _go_on(st: dict, job: str, e: dict, text: str) -> None:
    """Back to work after a decision (Euklides' or Marco's): what blocked the job starts over, and the
    worker is told what to do. A review that never gave a verdict is simply run again."""
    j = st["jobs"][job]
    if e.get("back"):  # a passing fault: the step runs again, the worker's work stands
        j["status"] = e["back"]
        return
    if e.get("kind") == "review":
        j.update(review_errors=0, status="needs_review")
        return
    _tell_over_budget(st, job)  # a worker is about to be paid for
    j.pop("auto_resumes", None)  # Euklides' or Marco's go: a new attempt
    if e.get("kind") == "integrate":
        j["integrate_fails"] = 0
    if e["trigger"] == "question":
        kind, msg = "answer", f"Question: {e['situation']}\n\nAnswer: {text}"
    else:
        kind, msg = ("integrate" if e.get("kind") == "integrate" else "euklides"), f"{e['situation']}\n\nNote: {text}"
    if j["session"]:
        j["resume_msg"] = brief.resume_message(kind, msg, st["slug"])
    j.pop("question", None)
    j.update(note=msg, status="needs_worker")


def _budget_usd(st: dict, job: str) -> float | None:
    """The job's planned budget from its plan, or None when the plan cannot be read now."""
    try:
        return next(mj["budget"]["usd"] for mj in load_plan(Path(st["plan"])).manifest["jobs"] if mj["id"] == job)
    except Exception:  # a missing plan must not stop an answer from going on
        return None


def _tell_over_budget(st: dict, job: str) -> None:
    """A worker resumes on a job whose spend is at or over its budget and what was raised: it is not blocked (Marco
    may have chosen to go on), but he hears it, once for each budget the job has had."""
    j = st["jobs"][job]
    budget = _budget_usd(st, job)
    if budget is None:
        return
    total = round(budget + j.get("usd_raised", 0), 4)
    if j["usd"] < total - 1e-9 or j.get("over_told") == total:
        return
    j["over_told"] = total
    run.alert(st, f"{job} goes on with a worker over its budget: ${j['usd']:.2f} spent of ${total:.2f}")


OTHER_ANSWERS = "Other answers: :stop blocks the job; any other text goes to the worker as its instruction."


def _then_text(st: dict, job: str, ask: dict) -> str:
    """The runner's words for what Marco's ok to Euklides' ask carries out."""
    then, tier = ask.get("then"), st["jobs"][job]["tier"]
    if then == "escalate":
        nxt = claude.NEXT_TIER.get(tier)
        return f"move {job} to {nxt} and go on" if nxt else f"go on at {tier} (the top tier) with the note above"
    if then == "widen":
        return f"widen {job}'s area to {', '.join(ask['files'])}"
    if then == "fix_base":
        return "fix the integration branch (a small worker, one fix(base) commit)"
    if then == "raise":
        return f"raise {job}'s budget by ${ask['usd']:.2f}"
    if then == "accept":
        return (f"accept {job}'s head on the gate `{ask['gate']}` (the runner runs it, no worker; red asks you "
                "again)")
    if then == "stop":
        return f"stop {job} (it ends blocked)"
    return "go on with the proposal above"


def _euklides_options(st: dict, job: str, ask: dict, d: dict, refuse: str | None) -> list[dict]:
    """The buttons of Euklides' ask: his proposal (its ok carries out `then`), his alternative if any (`ok 2`), then
    stop the job. A `then` of stop is the stop option and its ok; an ok the runner refuses leaves no first option."""
    stop = {"label": "Stop the job", "detail": f"{job} ends blocked", "answer": questions.STOP_JOB, "confirm": True}
    alt = ask.get("alternative")
    second = [{"label": alt["label"], "detail": _then_text(st, job, alt), "answer": questions.OK_2}] if alt else []
    if ask.get("then") == "stop":
        return [{**stop, "answer": "ok"}] + second
    if refuse or not (ask.get("then") or ask.get("proposal")):
        return second + [stop]
    label = _clean(str(d.get("label") or ask.get("proposal") or _then_text(st, job, ask)))
    return [{"label": label[:questions.MAX_LABEL], "detail": _then_text(st, job, ask), "answer": "ok"}, *second, stop]


def _foreign(st: dict, job: str, files: list[str], mjobs: dict | None) -> str | None:
    """Why a widen cannot be granted: a file inside another job's area. None when it can."""
    for other, mj in (mjobs or {}).items():
        hit = [f for f in files if other != job and match_any(f, run.area(st, other, mj))]
        if hit:
            return f"{', '.join(hit)} belong to {other}'s area, and a widen never takes another job's files"
    return None


def _euklides_ask(st: dict, job: str, e: dict, d: dict, decision: str | None, mjobs: dict | None = None) -> None:
    """Marco decides. The question ends with what his ok makes the runner do (`then`), what `ok 2` does when
    Euklides offered an alternative, and the other answers; a `then` the runner refuses (a widen into another job's
    area) says why instead, and ok is refused. Such an alternative is dropped, with an event."""
    j = st["jobs"][job]
    proposal = d["proposal"] or "(none: write what the job should do, or :stop)"
    ask = {"proposal": d["proposal"], "decision": decision, "ctx": e, "note": d["note"]}
    if d.get("then"):
        ask.update({k: d[k] for k in ("then", "files", "usd") if d.get(k) is not None})
        if d.get("gate") is not None:
            ask["gate"] = _clean(d["gate"])
    refuse = _foreign(st, job, ask["files"], mjobs) if ask.get("then") == "widen" else None
    alt = d.get("alternative") if d.get("then") else None
    if alt:
        alt = {k: alt[k] for k in ("label", "then", "files", "usd", "gate") if alt.get(k) is not None}
        alt["label"] = _clean(alt["label"])
        if alt.get("gate") is not None:
            alt["gate"] = _clean(alt["gate"])
        why = _foreign(st, job, alt["files"], mjobs) if alt["then"] == "widen" else None
        if why:
            run.event(st, f"{job}: Euklides' alternative was dropped: {why}")
        else:
            ask["alternative"] = alt
    tail = [OTHER_ANSWERS]
    if ask.get("alternative"):
        tail.insert(0, f"Answer ok 2 to: {_then_text(st, job, ask['alternative'])}")
    if refuse:
        ask["refuse_ok"] = f"ok cannot be granted: {refuse}"
        tail.insert(0, f"The widen cannot be granted: {refuse}. Answer :stop or say what the job should do.")
    elif ask.get("then"):
        tail.insert(0, f"Answer ok to: {_then_text(st, job, ask)}")
    tail_text = "\n\n".join(tail)
    options = _euklides_options(st, job, ask, d, refuse)
    head = _clean(f"{e['situation']}\n\nEuklides: {d['note']}\n\nProposal: {proposal}").strip()
    head = head[:max(0, questions.MAX_TEXT - len(tail_text) - 2)]  # the answer lines always survive the cut
    q = questions.ask(st, "worker", f"{head}\n\n{tail_text}", job=job, options=options)
    if refuse:
        q["refuse_ok"] = ask["refuse_ok"]
    j["euklides_ask"] = ask
    j["status"] = "waiting_answer"


def on_euklides(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    e = j.pop("euklides")
    d = euklides.parse(_answer(r) if _clean_exit(r) else None, e.get("allowed") or ["ask"])
    valid = d is not None
    d = d or NO_ANSWER
    try:
        decision = euklides.record(c.st, job, e, d, valid=valid, usd=_usd(r.get("usd")))
    except OSError as err:  # the journal is for learning, not for control
        decision = None
        run.event(c.st, f"{job}: the Euklides journal could not be written: {err}")
    run.event(c.st, f"{job} euklides: {d['action']} — {_clean(d['note'])[:300]}")
    if d["action"] == "ask":
        return _euklides_ask(c.st, job, e, d, decision, mjobs=c.mjobs)
    if d["action"] == "widen":
        return _widen(c, job, e, d, decision)
    if d["action"] == "fix_base":
        return _fix_base(c, job, e, d, decision)
    if d["action"] == "raise":
        return _raise(c, job, e, d, decision)
    if d["action"] == "regate":
        return _regate(c, job, e, d, decision)
    j["euklides_count"] = j.get("euklides_count", 0) + 1
    if d["action"] == "fixed":
        return _fixed(c, job, e, d, decision)
    if d["action"] == "escalate":
        _escalate(j)
    _go_on(c.st, job, e, d["note"])


def _raise(c: Ctx, job: str, e: dict, d: dict, decision) -> None:
    """Within what is left of his margin (a total across his raises), the job's budget grows and it goes on;
    past it, Marco decides, and his `ok` raises it without touching Euklides' margin."""
    j, budget = c.st["jobs"][job], c.mjobs[job]["budget"]["usd"]
    left = euklides.margin_left(j, budget, euklides.settings()["budget_margin"])
    if d["usd"] > left + 1e-9:
        proposal = f"raise {job}'s budget by ${d['usd']:.2f}"
        _euklides_ask(c.st, job, {**e, "situation": f"{e['situation']}\n\nEuklides would raise the budget by "
                                                    f"${d['usd']:.2f}, past the ${max(0, left):.2f} left of his margin"},
                      {**d, "proposal": proposal, "then": "raise"}, decision)
        return
    before = round(budget + j.get("usd_raised", 0), 4)
    j["usd_raised"] = round(j.get("usd_raised", 0) + d["usd"], 4)
    j["euklides_count"] = j.get("euklides_count", 0) + 1
    diag = d.get("diagnosis") or {}
    amend.add(c.st, job, "budget", before, round(budget + j["usd_raised"], 4), by="Euklides", decision=decision,
              why=f"{diag.get('cause', '')} → {diag.get('change', '')}" if diag else d["note"], undo="job")
    last = (j.get("failures") or [{}])[-1]
    j.setdefault("raises", []).append({"usd": d["usd"], "decision": decision, "ts": run.now(),
                                       "diagnosis": d.get("diagnosis"), "spent": j["usd"],
                                       "sig": last.get("sig"), "outcome": None})
    run.event(c.st, f"{job}: Euklides raised its budget by ${d['usd']:.2f} (${j['usd_raised']:.2f} raised in all)"
              + (f"; cause: {d['diagnosis']['cause'][:150]}; change: {d['diagnosis']['change'][:150]}"
                 if d.get("diagnosis") else ""))
    _go_on(c.st, job, e, d["note"])


def _raise_outcome(j: dict, outcome: str) -> None:
    """A raise's result, once the job ends: merged, or what stopped it. Only raises still open are set."""
    for r in j.get("raises") or []:
        if r.get("outcome") is None:
            r["outcome"] = outcome


FIXED_PROPOSAL = ":stop, or say what the job should do"


# ---- regate: the same gate again, alone on the machine ----------------------------------------

GATE_NAMES = {"gate": "job gate", "integrate": "integration gate"}
REGATE_STATUSES = ("needs_gate", "needs_integrate", "gating", "integrating")  # a flag here keeps the machine quiet
REGATE_PROPOSAL = ":stop, or say what the job should do (its gate is red even alone on the machine)"


def _regate_minutes(c: Ctx, job: str, kind: str) -> float:
    return c.mjobs[job]["budget"]["minutes"] if kind == "gate" else 2 * integrate.GATE_TIMEOUT / 60 + 30


def _regate(c: Ctx, job: str, e: dict, d: dict, decision) -> None:
    """The same gate again, alone on the machine: it waits for every gate to end and holds new gates and
    workers meanwhile (Slots.quiet). At most the gate's own deadline to get the machine."""
    j, red = c.st["jobs"][job], e["red"]
    j["euklides_count"] = j.get("euklides_count", 0) + 1
    j["regated"] = (j.get("regated") or []) + [f"{red['kind']}:{red['head']}"]
    j["regate"] = {"kind": red["kind"], "head": red["head"], "first_tail": str(red.get("tail", ""))[-1200:],
                   "note": d["note"], "decision": decision, "ctx": {k: v for k, v in e.items() if k != "allowed"},
                   "until": time.time() + _regate_minutes(c, job, red["kind"]) * 60}
    j["status"] = "needs_gate" if red["kind"] == "gate" else "needs_integrate"
    run.event(c.st, f"{job}: the {GATE_NAMES[red['kind']]} runs again alone on the machine; new gates and "
                    f"workers wait")


def _start_regate(c: Ctx, job: str, start) -> None:
    j = c.st["jobs"][job]
    try:
        if j["head"] != j["regate"]["head"]:
            raise RuntimeError(f"the job is on {str(j['head'])[:8]}, not on {j['regate']['head'][:8]} any more")
        start(c, job)
    except Exception as e:
        return _regate_ask(c, job, f"the quiet re-run could not start: {type(e).__name__}: {_clean(str(e))}")
    if j["status"] not in ("gating", "integrating"):  # start_integrate found the job merged already
        j.pop("regate", None)
        return
    run.event(c.st, f"{job}: quiet re-run started; nothing else is gating")


def _regate_ask(c: Ctx, job: str, why: str, tail: str = "") -> None:
    """Marco decides, with both red outputs."""
    j = c.st["jobs"][job]
    rg = j.pop("regate")
    second = f"\n\nThe quiet re-run:\n{_clean(tail)[-1200:]}" if tail else ""
    situation = f"{why}\n\nThe first red run:\n{rg['first_tail']}{second}"
    run.event(c.st, f"{job}: {why}")
    _euklides_ask(c.st, job, {**rg["ctx"], "situation": situation},
                  {"action": "ask", "note": f"regate: {rg['note']}", "undo": None, "why": "",
                   "proposal": REGATE_PROPOSAL}, rg.get("decision"))


def _flaky(c: Ctx, job: str, rg: dict) -> None:
    """A gate red under load and green alone: the run keeps it for decisions.md, the report, the final PR and
    the coach queue."""
    c.st.setdefault("flaky", []).append({"job": job, "kind": rg["kind"], "head": rg["head"], "note": rg["note"],
                                         "tail": rg["first_tail"][-600:], "at": run.now()})
    run.event(c.st, f"{job}: green alone on the machine: its {GATE_NAMES[rg['kind']]} is recorded as flaky")


def _fixed(c: Ctx, job: str, e: dict, d: dict, decision) -> None:
    """Euklides repaired the branch himself. Checked like a worker's DONE; then pushed (a rewrite with a backup
    and a lease) and gated, with no new worker attempt. A failed check asks Marco and pushes nothing."""
    j = c.st["jobs"][job]
    bad = done_check(c, job) if c.wt(job).is_dir() else ("worktree", "the job has no worktree")
    if _followup_settled(c, job, bad):
        return
    if bad:
        return _euklides_ask(c.st, job, e, {**d, "note": f"it replied fixed, but {bad[1]}",
                                            "proposal": FIXED_PROPOSAL, "then": "stop"}, decision)
    for k in ("question", "not_before", "push_tries", "step_tries"):
        j.pop(k, None)
    j.update(status="needs_push", head=gitops.rev(c.wt(job), "HEAD"), rewrite_ok=True, integrate_fails=0,
             review_errors=0)
    run.event(c.st, f"{job}: Euklides fixed the branch, head {j['head'][:8]}")


# ---- what Euklides can fix: widen a job's area, fix the base ----------------------------------

FIX_PREFIX = "_fix-"  # a job id never starts with "_": these names are the run's, not a job's
FIX_DONE = ("landed", "failed")
FIX_RUNNING = {"working": "worker", "gating": "gate", "reviewing": "review"}
FIX_WAITING_FOR = {"worker": "needs_worker", "gate": "needs_gate", "review": "needs_review"}
FIX_WAITING = ("needs_worker", "needs_gate", "needs_review", "needs_land")


def _small_fix(c: Ctx, files: list[str], lines: int | None, skip: tuple = ()) -> str | None:
    """None when the change is small; otherwise why not, in words Marco reads."""
    if len(files) > SMALL_FILES:
        return f"{len(files)} files, the small-fix rule allows {SMALL_FILES}"
    if lines is None or lines > SMALL_LINES:
        what = "a binary file" if lines is None else f"{lines} changed lines"
        return f"{what}, the small-fix rule allows {SMALL_LINES}"
    for other, mj in c.mjobs.items():
        if other in skip:
            continue
        hit = [f for f in files if match_any(f, run.area(c.st, other, mj))]
        if hit:
            return f"{', '.join(hit)} belong to {other}'s area"
    return None


def _legacy() -> bool:
    return euklides.settings()["mode"] == "legacy"


def _risk(c: Ctx, files: list[str], wt: Path, skip: tuple = ()) -> str | None:
    """None when Euklides may make this change without Marco; otherwise why not, in words Marco reads.
    legacy: the small-fix rule. blast-radius: the reserved list (reserved.py) on the change's diff, against the
    live areas of this run's other jobs and of every other run of the repo."""
    base = f"origin/{c.base}"
    if _legacy():
        why = _small_fix(c, files, gitops.changed_lines(wt, base, "HEAD", files), skip)
        return f"over the small-fix rule: {why}" if why else None
    areas = {other: run.area(c.st, other, mj) for other, mj in c.mjobs.items() if other not in skip}
    others = reserved.other_runs_areas(c.st["repo"], c.st["slug"])
    areas = None if others is None else {**areas, **others}
    hits = reserved.check(files, gitops.diff_text(wt, base, "HEAD", files), areas, euklides.settings()["reserved"])
    return f"reserved for Marco:\n{reserved.text(hits)}" if hits else None


def _big(wt: Path, base: str, files: list[str]) -> bool:
    """A change a reviewer sees before it lands (blast-radius): over REVIEW_FILES files or REVIEW_LINES lines."""
    lines = gitops.changed_lines(wt, base, "HEAD", files)
    return len(files) > reserved.REVIEW_FILES or lines is None or lines > reserved.REVIEW_LINES


def _outside(c: Ctx, job: str) -> list[str]:
    """What the job changed outside its area and its grants."""
    return [_printable(f) for f in gitops.changed_files(c.wt(job), f"origin/{c.base}", "HEAD")
            if not match_any(f, run.area(c.st, job, c.mjobs[job]))]


def _widen(c: Ctx, job: str, e: dict, d: dict, decision) -> None:
    """Grant the files the job changed outside its area, when the widen names them all and the change is small;
    otherwise Marco decides, and his `ok` grants them."""
    files, outside = d["files"], _outside(c, job)
    if not outside:
        why = "the job changes nothing outside its area now"
    elif not set(outside) <= set(files):
        why = f"the job also changed {', '.join(sorted(set(outside) - set(files)))}, which the widen did not name"
    else:
        why = _risk(c, outside, c.wt(job), skip=(job,))
    if why:
        grant = outside or files
        d = {**d, "proposal": d["proposal"] or f"widen {job}'s area to {', '.join(grant)}", "then": "widen",
             "files": grant}
        why = why[0].upper() + why[1:]
        _euklides_ask(c.st, job, {**e, "situation": f"{e['situation']}\n\n{why}. "
                                                    f"Files: {', '.join(grant)}"}, d, decision, mjobs=c.mjobs)
        return
    c.st["jobs"][job]["euklides_count"] = c.st["jobs"][job].get("euklides_count", 0) + 1
    _grant(c.st, job, outside, decision)


def _absorb_companions(c: Ctx, job: str, outside: list[str]) -> list[str]:
    """Companion files (tests, test helpers, docs) among `outside` become grants when, together, they are within
    the small-fix rule and none lies in another job's area; no Euklides, no question. Returns what still blocks."""
    if c.mjobs[job].get("companions") is False:
        return outside
    comps = [f for f in outside if companions.is_companion(f)]
    if not comps:
        return outside
    if _risk(c, comps, c.wt(job), skip=(job,)):
        return outside
    _add_grant(c.st, job, comps, "companion files clear of the reserved list" if not _legacy() else
               "companion files within the small-fix rule", by="runner (companion files)")
    run.event(c.st, f"{job}: companion files allowed outside its area: {', '.join(comps)}")
    return [f for f in outside if f not in comps]


def grants_file(st: dict, job: str) -> Path:
    return paths.run_dir(st["repo"], st["slug"]) / f"{job}.grants.json"


def _write_grants(st: dict, job: str) -> None:
    """The job's grants, where its area-guard reads them: a worker already running sees a grant at once."""
    run._atomic(grants_file(st, job), json.dumps((st.get("grants") or {}).get(job, [])) + "\n")


def _add_grant(st: dict, job: str, files: list[str], decision, by: str) -> None:
    grants = st.setdefault("grants", {}).setdefault(job, [])
    before = list(grants)
    grants += [f for f in files if f not in grants]
    _write_grants(st, job)
    journaled = by in ("Euklides", "Marco")  # the runner's companions pass a reason, not a journal id
    amend.add(st, job, "area", before, list(grants), by=by.split(" (")[0], why=("" if journaled else decision),
              decision=decision if journaled else None, undo="job")
    run.alert(st, f"{job}: {by} widened its area to {', '.join(files)} ({decision or 'not journaled'})")


def _grant(st: dict, job: str, files: list[str], decision, by: str = "Euklides") -> None:
    """The files join the job's area for this run; the worker's head goes on as if it had stayed inside."""
    _add_grant(st, job, files, decision, by)
    st["jobs"][job].update(status="needs_push", head=gitops.rev(_wt(st, job), "HEAD"))


def _fix_base(c: Ctx, job: str, e: dict, d: dict, decision) -> None:
    _start_base_fix(c.st, job, e, d, decision)


def _start_base_fix(st: dict, job: str, e: dict, d: dict, decision, by: str = "Euklides") -> None:
    """One small worker fixes the integration branch; every job that finds the base red meanwhile waits on it.
    Marco's ok (`by="Marco"`) has no count limit and counts against neither Euklides' fixes nor his decisions."""
    fixes = st.setdefault("fixes", [])
    live = next((f for f in fixes if f["status"] not in FIX_DONE), None)
    if live is None:
        if by == "Euklides":
            st["fix_base_count"] = st.get("fix_base_count", 0) + 1
        live = {"n": len(fixes) + 1, "job": job, "note": _clean(d["note"]), "situation": e["situation"],
                "decision": decision, "status": "needs_worker", "waiting": {}, "lost": 0, "by": by}
        fixes.append(live)
        run.alert(st, f"{by} is fixing the base (fix {live['n']}, for {job}): {_clean(d['note'])[:200]}")
    live["waiting"][job] = {**e, "decision": decision, "proposal": d["proposal"] or d["note"]}
    st["jobs"][job].update(status="waiting_fix", fix=live["n"])
    if by == "Euklides":
        st["jobs"][job]["euklides_count"] = st["jobs"][job].get("euklides_count", 0) + 1


def _fix_of(st: dict, name: str) -> dict | None:
    try:
        n = int(name.split(".")[0].removeprefix(FIX_PREFIX))
    except ValueError:
        return None
    return next((f for f in st.get("fixes") or [] if f["n"] == n), None)


def _start_fix_worker(c: Ctx, f: dict) -> None:
    name = f"{FIX_PREFIX}{f['n']}"
    wt, m = c.wt(name), c.plan.manifest
    gitops.fetch(c.repo)
    gitops.add_worktree(c.repo, wt, c.branch(name), f"origin/{c.base}")  # ark/<slug>-_fix-<n>: no job's name
    prompt = brief.fix_base_brief(c.st, f, c.plan, SMALL_FILES, SMALL_LINES, legacy=_legacy())
    env = claude.runner_env("worker", repo_name=c.st["repo"], origin_url=gitops.origin_url(c.repo), worktree=wt,
                            area=json.dumps(["**"]), report=c.report(name), gate=m["integration_gate"],
                            run_dir=c.dir, job=name)
    sid = str(uuid.uuid4())
    cmd = claude.agent_cmd("worker", claude.MODELS["T1"], prompt, session=sid, resume=False, usd=FIX_BASE_USD)
    proc.start(c.dir, f"{name}.worker", cmd, cwd=wt, env=env, minutes=30, pre=list(m.get("setup") or []),
               meta={"session": sid})
    f["status"] = "working"
    run.event(c.st, f"base fix {f['n']}: worker started")


def _start_fix_gate(c: Ctx, f: dict) -> None:
    name = f"{FIX_PREFIX}{f['n']}"
    env = gate_env(c, "gate")  # it runs tests a worker wrote
    proc.start(c.dir, f"{name}.gate", ["sh", "-c", c.plan.manifest["integration_gate"]], cwd=c.wt(name), env=env,
               minutes=integrate.GATE_TIMEOUT / 60, meta={"head": f.get("head")})
    f["status"] = "gating"


def _start_fix_review(c: Ctx, f: dict) -> None:
    """A big base fix (blast-radius) is reviewed before it lands: CLEAN lands it, anything else fails it."""
    name = f"{FIX_PREFIX}{f['n']}"
    wt = c.wt(name)
    prompt = brief.fix_review_brief(c.st, f, base=f"origin/{c.base}", head=f["head"])
    cmd = claude.agent_cmd("reviewer", claude.MODELS["T1"], prompt, session=str(uuid.uuid4()), usd=FIX_BASE_USD / 2)
    env = claude.runner_env("reviewer", repo_name=c.st["repo"], origin_url=gitops.origin_url(c.repo), worktree=wt,
                            area=json.dumps(["**"]), job=name, run_dir=c.dir)
    proc.start(c.dir, f"{name}.review", cmd, cwd=wt, env=env, minutes=30, meta={"head": f["head"]})
    f["status"] = "reviewing"


def _move_fixes(c: Ctx, slots: Slots) -> None:
    """The live base fix one step on. A step that raises is a passing fault: it waits and tries again on the
    PASSING_WAITS schedule, then the fix fails and its jobs ask Marco."""
    for f in c.st.get("fixes") or []:
        if f["status"] not in FIX_WAITING or f.get("not_before", 0) > time.time():
            continue
        try:
            if f["status"] == "needs_worker":
                if not slots.writer(c.st["repo"]):
                    continue
                try:
                    _start_fix_worker(c, f)
                finally:
                    if f["status"] != "working":
                        slots.release_writer(c.st["repo"])
            elif f["status"] == "needs_gate":
                if not slots.gate(True, c.st, "fix"):
                    continue
                _start_fix_gate(c, f)
            elif f["status"] == "needs_review":
                _start_fix_review(c, f)
            else:
                _land_fix(c, f)
        except Exception as e:
            _fix_retry(c, f, f"{type(e).__name__}: {_clean(str(e))}")
        else:
            f.pop("tries", None)
            f.pop("not_before", None)


def _fix_retry(c: Ctx, f: dict, why: str) -> None:
    n = f["tries"] = f.get("tries", 0) + 1
    if n > len(PASSING_WAITS):
        return _fix_failed(c, f, f"its step kept failing: {why}")
    f["not_before"] = time.time() + PASSING_WAITS[n - 1]
    if n == 1:
        run.event(c.st, f"base fix {f['n']}: {why} (retrying in a minute)")


def on_fix(c: Ctx, name: str, r: dict) -> None:
    f = _fix_of(c.st, name)
    kind = name.rpartition(".")[2]
    if f is None or FIX_RUNNING.get(f["status"]) != kind:
        return run.event(c.st, f"ignored the result of {_clean(name)}: nothing was waiting for it")
    wt = c.wt(f"{FIX_PREFIX}{f['n']}")
    if kind == "worker":
        if not _clean_exit(r):
            return _fix_failed(c, f, f"the fix worker ended without finishing: {r['reason'] or 'exit ' + str(r['exit'])}")
        if not gitops.is_clean(wt):
            return _fix_failed(c, f, "the fix worker left uncommitted changes")
        files = [_printable(x) for x in gitops.changed_files(wt, f"origin/{c.base}", "HEAD")]
        if not files:
            return _fix_failed(c, f, "the fix worker changed nothing")
        skip = tuple(j for j, s in c.st["jobs"].items() if s["status"] == "merged")
        why = _risk(c, files, wt, skip=skip)
        if why:
            return _fix_failed(c, f, why)
        review = not _legacy() and _big(wt, f"origin/{c.base}", files)
        f.update(status="needs_gate", head=gitops.rev(wt, "HEAD"), files=files, review=review)
        run.event(c.st, f"base fix {f['n']}: worker done ({', '.join(files)})"
                        f"{'; a reviewer sees it before it lands' if review else ''}")
        return
    if kind == "review":
        answer = _review_answer(r)
        if answer is None or answer[0] != "CLEAN":
            found = "; ".join(str(x) for x in answer[1])[:800] if answer else "the reviewer gave no verdict"
            return _fix_failed(c, f, f"the reviewer did not pass the fix: {found}")
        f["status"] = "needs_land"
        run.event(c.st, f"base fix {f['n']}: review CLEAN")
        return
    if r["exit"] != 0 or r["reason"]:
        tail = _clean(r.get("result") or "")[-800:]
        return _fix_failed(c, f, f"the integration gate is still red after the fix "
                                 f"(exit {r['exit']}{', ' + r['reason'] if r['reason'] else ''}):\n{tail}")
    f["status"] = "needs_review" if f.get("review") else "needs_land"
    run.event(c.st, f"base fix {f['n']}: integration gate green")


def _land_fix(c: Ctx, f: dict) -> None:
    """One fix(base) commit on ark/<slug>, under the run's merge lock; then its jobs integrate again."""
    scratch = c.wt(f"{FIX_PREFIX}{f['n']}-land")
    try:
        with merge.run_lock(c.dir):
            gitops.fetch(c.repo)
            base = gitops.rev(c.repo, f"origin/{c.base}")
            sha = gitops.squash_keep(c.repo, base, f["head"],
                                     f"fix(base): {_clean(f['note'])[:72]} (Euklides fix {f['n']})", scratch)
            if sha is None:
                return _fix_failed(c, f, "the fix does not apply on the integration branch any more")
            if sha != base:
                gitops.push_ref(c.repo, sha, c.base)
    finally:
        gitops._cleanup(c.repo, scratch)
    f["status"] = "landed"
    amend.add(c.st, ",".join(f["waiting"]) or "-", "base", base[:8], sha[:8], by=f.get("by") or "Euklides",
              decision=f.get("decision"), why=f"fix {f['n']}: {f['note']} ({', '.join(f['files'])})"
              + (" · reviewed" if f.get("review") else ""), undo="run")
    run.alert(c.st, f"base fix {f['n']} landed on {c.base}: {', '.join(f['files'])} "
                    f"({f['decision'] or 'not journaled'})")
    for job, e in f["waiting"].items():
        j = c.st["jobs"][job]
        if j["status"] == "waiting_fix" and j.get("fix") == f["n"]:
            j.pop("fix", None)
            if e.get("kind") == "integrate":
                j.update(status="needs_integrate", integrate_fails=0)
            else:  # Marco's ok started the fix from another trigger: the worker goes on, on the fixed base
                _go_on(c.st, job, e, f"base fix {f['n']} landed on {c.base}; bring origin/{c.base} into the "
                                     f"job's branch first. {e.get('proposal') or ''}".strip())
    gitops._cleanup(c.repo, c.wt(f"{FIX_PREFIX}{f['n']}"))


def _fix_failed(c, f: dict, why: str, st: dict | None = None) -> None:
    """Every job still waiting on the fix asks Marco, with Euklides' proposal and the reason."""
    st = st if st is not None else c.st
    f["status"], f["why"] = "failed", _clean(why)[:1000]
    f.pop("not_before", None)
    run.alert(st, f"base fix {f['n']} failed: {f['why'][:300]}")
    for job, e in f["waiting"].items():
        j = st["jobs"][job]
        if j["status"] != "waiting_fix" or j.get("fix") != f["n"]:
            continue
        j.pop("fix", None)
        _euklides_ask(st, job, {**e, "situation": f"{e['situation']}\n\nBase fix {f['n']} failed: {f['why']}"},
                      {"note": f"base fix {f['n']} failed: {f['why']}", "proposal": e["proposal"],
                       "then": "fix_base"}, e["decision"])
    if c is not None:
        gitops._cleanup(c.repo, c.wt(f"{FIX_PREFIX}{f['n']}"))


# ---- the base follows main: a check on main merged into ark/<slug>, landed only when green ----------

_refresh_seen: dict[tuple, float] = {}  # the last look at origin's default branch, per run


def _plan_heavy(st: dict) -> bool:
    return any(j.get("heavy") for j in st["jobs"].values())


RECHECK_WAIT = 60  # seconds before the one automatic re-check of a refresh whose gate was red on a clean merge


def _new_refresh(n: int, main: str | None, before: str | None) -> dict:
    return {"n": n, "main": main, "status": "needs_check", "base": None, "merged": None, "conflict": [], "tail": "",
            "seen_before": before}


REFRESH_DEFAULT = ("T1", 40)  # tier and minutes of a refresh worker when the plan has no merge entry


def _refresh_budget(c: Ctx) -> tuple[str, float, float]:
    """(tier, usd left, minutes) of a refresh worker: the plan's merge entry, spent across the run's refreshes,
    or the workspace's limits.refresh_usd for each refresh."""
    g = c.plan.manifest.get("merge")
    if isinstance(g, dict):
        b = g["budget"]
        return g["tier"], round(b["usd"] - c.st.get("merge_usd", 0), 4), b["minutes"]
    tier, minutes = REFRESH_DEFAULT
    return tier, paths.load_workspace()["limits"]["refresh_usd"], minutes


def _refresh_of(st: dict, name: str) -> dict | None:
    """The live refresh, when `name` is its process."""
    r = st.get("refresh")
    try:
        n = int(name.split(".")[0].removeprefix(REFRESH_PREFIX))
    except ValueError:
        return None
    return r if r is not None and r.get("n") == n else None


def _finish_refresh(st: dict, r: dict, result: str, why: str = "", alert: bool = False) -> None:
    """The refresh ends: it goes into st["refreshes"] and the run has no live one."""
    st.setdefault("refreshes", []).append({"n": r["n"], "main": r["main"], "result": result,
                                           "usd": r.get("usd", 0.0), "at": run.now()})
    if st.get("refresh") is r:
        st.pop("refresh")
    if r.get("workers"):  # its worktree goes at the next _move_refresh, where the repo is at hand
        st.setdefault("refresh_cleanup", []).append({"name": f"{REFRESH_PREFIX}{r['n']}",
                                                     "landed": result == "landed"})
    if why:
        text = f"refresh {r['n']} (main@{str(r['main'])[:8]}) {result}: {_clean(why)[:1000]}"
        if alert or (result == "failed" and not st.get("base_green")):  # every job holds on it: Marco hears it
            run.alert(st, text)
        else:
            run.event(st, text)


def _main_moved(c: Ctx) -> str | None:
    """origin's default branch, when it moved since the last refresh started and none is live; at most one look
    every REFRESH_EVERY seconds, so several moves between looks give one refresh, to the newest."""
    st = c.st
    if st.get("refresh") or st.get("closing") or st.get("ending"):
        return None
    key = (str(paths.ark_home()), st["repo"], st["slug"])
    if time.monotonic() - _refresh_seen.get(key, -math.inf) < REFRESH_EVERY:
        return None
    _refresh_seen[key] = time.monotonic()
    sha = gitops.remote_sha(c.repo, st.get("default_branch") or gitops.default_branch(c.repo))
    return sha if sha and sha != st.get("main_seen") else None


def _start_refresh_check(c: Ctx, r: dict) -> None:
    name = f"{REFRESH_PREFIX}{r['n']}"
    cmd = [str(paths.code_dir() / "bin" / "ark-refresh"), f"{c.st['repo']}/{c.st['slug']}", r["main"]]
    env = gate_env(c, "integrate")  # the plan's gate
    proc.start(c.dir, f"{name}.check", cmd, cwd=paths.code_dir(), env=env,
               minutes=2 * integrate.GATE_TIMEOUT / 60 + 30, meta={"main": r["main"]})
    r["status"] = "checking"


def _move_refresh(c: Ctx, slots: Slots) -> None:
    """A new refresh when main moved; the live one a step on; a finished refresh's worktree goes."""
    try:
        _step_refresh(c, slots)
    finally:
        _clean_refreshes(c)


def _step_refresh(c: Ctx, slots: Slots) -> None:
    """A step that raises waits and tries again on the PASSING_WAITS schedule, then the refresh fails."""
    st = c.st
    r = st.get("refresh")
    if r is None:
        try:
            main = _main_moved(c)
        except STEP_ERRORS as e:  # offline: the next look tries again
            return run.event(st, f"cannot look at main: {_clean(str(e))[:300]}")
        if main is None:
            return
        n = 1 + max((x["n"] for x in st.get("refreshes") or []), default=0)
        r = st["refresh"] = _new_refresh(n, main, st.get("main_seen"))
        st["main_seen"] = main
        run.event(st, f"refresh {n}: main moved to {main[:8]}; checking it merged into {c.base}")
    if r.get("not_before", 0) > time.time():
        return
    try:
        if r["status"] == "clash":
            if r.pop("fresh", None):  # reported in this cycle; acted on from the next one
                return
            _after_clash(c, r)
        if r["status"] == "needs_check":
            if not slots.gate(_plan_heavy(st), st, "refresh"):
                return
            _start_refresh_check(c, r)
        elif r["status"] == "needs_worker":
            if not slots.writer(st["repo"]):
                return
            try:
                _start_refresh_worker(c, r)
            finally:
                if r["status"] != "working":
                    slots.release_writer(st["repo"])
        elif r["status"] == "needs_gate":
            if not slots.gate(_plan_heavy(st), st, "refresh"):
                return
            _start_refresh_gate(c, r)
        elif r["status"] == "needs_land":
            _land_refresh(c, r)
        else:
            return
    except Exception as e:
        _refresh_retry(c, r, f"{type(e).__name__}: {_clean(str(e))}")
    else:
        r.pop("tries", None)
        r.pop("not_before", None)


def _refresh_name(r: dict) -> str:
    return f"{REFRESH_PREFIX}{r['n']}"


def _after_clash(c: Ctx, r: dict) -> None:
    """A clash goes to a refresh worker while the budget lasts; past it, Marco decides."""
    _, left, _ = _refresh_budget(c)
    if left > 0:
        r["status"] = "needs_worker"
        return run.event(c.st, f"refresh {r['n']}: a refresh worker takes the clash (${left:.2f} left)")
    _ask_refresh(c.st, r, f"the refresh budget is spent (${c.st.get('merge_usd', 0):.2f} spent), so no worker "
                          "started")


def _refresh_full_usd(c: Ctx) -> float:
    """What one refresh worker may spend after Marco answers retry: the whole budget again."""
    g = c.plan.manifest.get("merge")
    return g["budget"]["usd"] if isinstance(g, dict) else paths.load_workspace()["limits"]["refresh_usd"]


def _start_refresh_worker(c: Ctx, r: dict) -> None:
    name = _refresh_name(r)
    wt, m = c.wt(name), c.plan.manifest
    tier, left, minutes = _refresh_budget(c)
    usd = _refresh_full_usd(c) if r.get("retry") else left
    if usd <= 0:
        return _ask_refresh(c.st, r, "the refresh budget is spent, so no worker started")
    gitops.fetch(c.repo)
    fresh = not wt.exists()
    gitops.add_worktree(c.repo, wt, c.branch(name), f"origin/{c.base}")  # ark/<slug>-_refresh-<n>: no job's name
    if fresh or not r.get("from"):
        r["base"] = r["from"] = gitops.rev(wt, "HEAD")  # what the worker starts from: the base it lands on
    default = c.st.get("default_branch") or gitops.default_branch(c.repo)
    prompt = brief.refresh_brief(c.st, r, c.plan, default)
    env = claude.runner_env("worker", repo_name=c.st["repo"], origin_url=gitops.origin_url(c.repo), worktree=wt,
                            area=json.dumps(["**"]), report=c.report(name), gate=m["integration_gate"],
                            run_dir=c.dir, job=name)
    sid = str(uuid.uuid4())
    cmd = claude.agent_cmd("worker", claude.MODELS[tier], prompt, session=sid, resume=False, usd=usd)
    proc.start(c.dir, f"{name}.worker", cmd, cwd=wt, env=env, minutes=minutes, pre=list(m.get("setup") or []),
               meta={"session": sid, "tier": tier})
    r.pop("retry", None)
    r["workers"] = r.get("workers", 0) + 1
    r["status"] = "working"
    run.event(c.st, f"refresh {r['n']}: worker started ({tier}, up to ${usd:.2f})")


def _start_refresh_gate(c: Ctx, r: dict) -> None:
    name = _refresh_name(r)
    env = gate_env(c, "gate")  # it runs tests a worker wrote
    proc.start(c.dir, f"{name}.gate", ["sh", "-c", c.plan.manifest["integration_gate"]], cwd=c.wt(name), env=env,
               minutes=integrate.GATE_TIMEOUT / 60, meta={"head": r.get("head")})
    r["status"] = "gating"


def _ask_refresh(st: dict, r: dict, why: str) -> None:
    """Marco decides: one more worker, or the base stays until main moves again."""
    r["status"], r["why"] = "waiting_answer", _clean(why)[:1200]
    questions.ask(st, "refresh", _refresh_question(r), options=REFRESH_OPTIONS)


REFRESH_OPTIONS = [{"label": "Retry", "detail": "one more refresh worker with the same budget", "answer": "retry"},
                   {"label": "Skip", "detail": "the base stays until main moves again", "answer": "skip"}]


def _refresh_question(r: dict) -> str:
    return (f"Refresh {r['n']}: main moved to {str(r['main'])[:12]} and does not merge into the run's base on its "
            f"own; {r.get('why') or 'the refresh stopped'}. The base stays where it is meanwhile. Answer `retry` "
            "to start one more refresh worker with the same budget again (the extra spend is yours to approve), "
            "or `skip` to leave the base as it is until main moves again.")


def _clean_refreshes(c: Ctx) -> None:
    """A finished refresh's worktree goes; its branch on origin too, only when it landed."""
    for item in c.st.pop("refresh_cleanup", None) or []:
        gitops._cleanup(c.repo, c.wt(item["name"]))
        if item.get("landed"):  # the worker does not send it, so usually there is nothing to remove
            try:
                gitops.git(c.repo, "push", "-q", "origin", "--delete", c.branch(item["name"]), check=False)
            except (gitops.GitError, OSError):
                pass


def _refresh_retry(c: Ctx, r: dict, why: str) -> None:
    n = r["tries"] = r.get("tries", 0) + 1
    if n > len(PASSING_WAITS):
        return _finish_refresh(c.st, r, "failed", f"its step kept failing: {why}", alert=True)
    r["not_before"] = time.time() + PASSING_WAITS[n - 1]
    if n == 1:
        run.event(c.st, f"refresh {r['n']}: {why} (retrying in a minute)")


def on_refresh(c: Ctx, name: str, r: dict) -> None:
    """ark-refresh's JSON line: green waits to land; a red main keeps the base; a clash waits for its fix."""
    st = c.st
    live = _refresh_of(st, name)
    kind = name.rpartition(".")[2]
    if live is None or REFRESH_RUNNING.get(live["status"]) != kind:
        return run.event(st, f"ignored the result of {_clean(name)}: nothing was waiting for it")
    if kind == "worker":
        return _on_refresh_worker(c, live, r)
    if kind == "gate":
        return _on_refresh_gate(c, live, r)
    out = None if r["reason"] else _answer(r)
    if not isinstance(out, dict) or "ok" not in out:
        return _finish_refresh(st, live, "failed",
                               f"ark-refresh ended without a result ({r['reason'] or 'no JSON line'})")
    main, tail = live["main"], _clean(str(out.get("tail") or ""))
    if out["ok"] is True and isinstance(out.get("base"), str) and isinstance(out.get("merged"), str):
        live.update(status="needs_land", base=out["base"], merged=out["merged"])
        if st.pop("main_red", None):
            run.event(st, f"main is green again at {main[:8]}")
        if live.get("first_tail") is not None:  # red on the first look, green on the second: flaky, no worker paid
            _flaky_refresh(st, live, main)
        return run.event(st, f"refresh {live['n']}: main@{main[:8]} merged into {c.base} is green")
    if out.get("main_red") is True:
        if st.get("main_red") != main:
            base = str(out.get("base") or "")
            run.alert(st, f"main is red at {main[:8]}; the base stays at {base[:8]}\n{tail[-800:]}")
        st["main_red"] = main
        return _finish_refresh(st, live, "main_red")
    if not out.get("conflict") and out.get("main_red") is False and live.get("first_tail") is None:
        # The merge is textually clean and only the gate is red, with main alone green: it may be the machine (load,
        # a timing test) and not the merge. One more look, through the usual gate slot and load brake, before a
        # worker is paid for it. A conflict is deterministic, so it never comes here.
        live.update(status="needs_check", first_tail=tail[-1200:], not_before=time.time() + RECHECK_WAIT)
        return run.event(st, f"refresh {live['n']}: the gate is red on main@{main[:8]} merged into {c.base}, with a "
                             "clean merge; checking once more before any worker")
    if out.get("conflict") or out.get("main_red") is False:
        conflict = [_printable(str(f)) for f in out.get("conflict") or []]
        live.update(status="clash", conflict=conflict, merged=out.get("merged"), base=out.get("base"),
                    tail=tail[-4000:], fresh=True)
        what = ", ".join(conflict) if conflict else "gate red on the merge"
        return run.event(st, f"refresh {live['n']}: main@{main[:8]} clashes with the run ({what})")
    _finish_refresh(st, live, "failed", str(out.get("reason") or "ark-refresh failed")
                    + (f"\n{tail[-800:]}" if tail else ""))


def _flaky_refresh(st: dict, live: dict, main: str) -> None:
    """A refresh gate red on the first look and green on the second: recorded like a job's flaky gate, so the
    decisions, the report and the coach queue show it."""
    st.setdefault("flaky", []).append({"job": _refresh_name(live), "kind": "integrate", "head": main,
                                       "note": "the integration gate of a base refresh was red on the first look and "
                                               "green on the second, with no change",
                                       "tail": str(live["first_tail"])[-600:], "at": run.now()})
    run.event(st, f"refresh {live['n']}: green on the second look: its integration gate is recorded as flaky")


def _on_refresh_worker(c: Ctx, live: dict, r: dict) -> None:
    """The worker's spend is the merge budget's; its branch must hold both the base and main, committed."""
    st, usd = c.st, _usd(r.get("usd"))  # _charge already put it in other_usd
    st["merge_usd"] = round(st.get("merge_usd", 0) + usd, 4)
    live["usd"] = round(live.get("usd", 0) + usd, 4)
    wt = c.wt(_refresh_name(live))
    status, detail = brief.worker_status(r.get("result") or "")
    if not _clean_exit(r) or status != "DONE":
        why = r["reason"] or (f"exit {r['exit']}" if r["exit"] else f"{status}: {detail}")
        return _ask_refresh(st, live, f"the refresh worker ended without finishing ({_clean(str(why))[:300]})")
    if not gitops.is_clean(wt):
        return _ask_refresh(st, live, "the refresh worker left uncommitted changes")
    head = gitops.rev(wt, "HEAD")
    if not (gitops.is_ancestor(wt, live["base"], head) and gitops.is_ancestor(wt, live["main"], head)):
        return _ask_refresh(st, live, "the worker did not merge main")
    live.update(head=head, status="needs_gate")
    run.event(st, f"refresh {live['n']}: the worker joined main@{live['main'][:8]} and the base (${usd:.2f})")


def _on_refresh_gate(c: Ctx, live: dict, r: dict) -> None:
    if r["exit"] != 0 or r["reason"]:
        tail = _clean(r.get("result") or "")[-800:]
        return _ask_refresh(c.st, live, "the integration gate is red on the worker's result "
                                        f"(exit {r['exit']}{', ' + r['reason'] if r['reason'] else ''}):\n{tail}")
    live.update(merged=live["head"], status="needs_land")
    run.event(c.st, f"refresh {live['n']}: integration gate green on the worker's result")


def _answered_refresh(st: dict, q: dict, text: str) -> None:
    r = st.get("refresh")
    if not r or r["status"] != "waiting_answer":
        return run.event(st, f"{q['id']} was answered, but no refresh waits for it")
    word = text.strip().lower()
    if word == "retry":
        r.update(status="needs_worker", retry=True)
        return run.event(st, f"refresh {r['n']}: Marco asked for one more worker")
    if word == "skip":
        return _finish_refresh(st, r, "skipped", "Marco skipped it; the base stays until main moves again")
    questions.ask(st, "refresh", _refresh_question(r), options=REFRESH_OPTIONS)


def _land_refresh(c: Ctx, r: dict) -> None:
    """The green merge onto ark/<slug>, under the run's merge lock, only while the base is the one checked."""
    st = c.st
    with merge.run_lock(c.dir):
        gitops.fetch(c.repo)
        if gitops.rev(c.repo, f"origin/{c.base}") != r["base"]:
            return _stale_refresh(st, r, f"{c.base} moved since the check")
        if r["merged"] != r["base"]:  # otherwise main was in it already: nothing to send
            try:
                gitops.push_ref(c.repo, r["merged"], c.base)
            except gitops.GitError as e:
                return _stale_refresh(st, r, f"origin refused the new base: {_clean(str(e))[:300]}")
    st["base_green"] = True
    if st.pop("main_red", None):
        run.event(st, f"main is green again at {r['main'][:8]}")
    _finish_refresh(st, r, "landed")
    run.event(st, f"base refreshed to main@{r['main'][:8]}" if r["n"] else f"base green at main@{r['main'][:8]}")


def _stale_refresh(st: dict, r: dict, why: str) -> None:
    """The base moved under the refresh: the next look starts again."""
    st["main_seen"] = r.get("seen_before")
    _finish_refresh(st, r, "stale", f"{why}; trying again")


def _reconcile_refresh(st: dict, d: Path) -> None:
    """A check, worker or gate whose process is gone with no receipt starts again once; the second time the
    refresh fails. One running that the state does not know about is adopted."""
    r = st.get("refresh")
    if not r:
        return
    for running, kind in REFRESH_RUNNING.items():
        if r["status"] == REFRESH_WAITING[kind] and (d / f"{REFRESH_PREFIX}{r['n']}.{kind}.proc").exists():
            r["status"] = running
            return run.event(st, f"refresh {r['n']}: found its {kind} running; the state had not recorded it")
    kind = REFRESH_RUNNING.get(r["status"])
    if kind is None or (d / f"{REFRESH_PREFIX}{r['n']}.{kind}.proc").exists():
        return
    r["lost"] = r.get("lost", 0) + 1
    if r["lost"] >= 2:
        return _finish_refresh(st, r, "failed", f"its {kind} was lost twice without a result")
    r["status"] = REFRESH_WAITING[kind]
    run.event(st, f"refresh {r['n']}: its {kind} is gone without a result; starting it again")


DECISIONS = "_decisions.tick"  # the close-of-run tick belongs to the run, not to a job
MODEL_KINDS = ("worker", "review", "euklides")  # the others run a program of ours, not the model
HANDLERS = {"worker": on_worker, "gate": on_gate, "review": on_review, "integrate": on_integrate,
            "euklides": on_euklides}


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
        j.pop("attaching", None)
        j.pop("steering", None)
        j.pop("stop_then", None)
        j["setup_done"] = True  # no reason on the receipt: setup got through
        j["attempts"] = max(0, j["attempts"] - 1)
        j["session"] = None  # the same death as a session that cannot be resumed: the next try starts fresh
    if kind == "euklides":  # nobody else to send it to: back off without end, and tell Marco early
        n = _wait_longer(j, "infra_tries")
        if n == 2:
            run.alert(c.st, f"{job}: Euklides' model is unreachable (offline or auth expired?): nothing is charged, "
                            "it keeps retrying with a growing wait", critical=True)
        return run.event(c.st, f"{job}: its euklides got no answer from the model; try {n}")
    _passing(c, job, "infra_tries", f"its {kind} got no answer from the model (offline or auth expired?)",
             WAITING[kind])


def _slept_through(j: dict, kind: str) -> bool:
    """Whether this process lived through a sleep. The mark is spent by its receipt, whatever it says."""
    marks = j.get("slept_through") or {}
    hit = marks.pop(kind, None) is not None
    if not marks:
        j.pop("slept_through", None)
    return hit


def _clean_success(kind: str, r: dict) -> bool:
    if not _clean_exit(r):
        return False
    if kind == "review":
        return _review_answer(r) is not None
    if kind in ("euklides", "intervene"):
        return isinstance(_answer(r), dict)
    if kind == "integrate":
        out = _answer(r)
        return isinstance(out, dict) and "ok" in out
    return True


def _free_retry(c: Ctx, job: str, kind: str, r: dict) -> None:
    """It lived through a sleep and failed: the network most likely went with the lid. Nothing is charged and
    there is no backoff: the wait for the network after the wake already covered that."""
    j = c.st["jobs"][job]
    j["status"] = WAITING[kind]
    if kind == "worker":
        j["attempts"] = max(0, j["attempts"] - 1)
        j["session"] = r.get("session")
        j["resume_msg"] = brief.resume_message("wake", "Check `git status` and your report first.", c.st["slug"]) \
            if j["session"] else None
    why = _clean(r["reason"] or f"exit {r['exit']}")
    run.event(c.st, f"{job}: its {kind} failed after the machine slept ({why}); trying again, nothing charged")


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
        if j.get("errors", 0) > ERROR_ALERT_AFTER and proc.discard(c.dir, name):
            kind = name.rpartition(".")[2]
            why = f"its {kind}'s result could not be archived for {ERROR_ALERT_AFTER} cycles: {_clean(str(e))}"
            j.pop("errors", None)
            j.pop("error", None)
            if kind == "euklides":  # never send Euklides' own trouble back to him
                e_ = j.pop("euklides", None) or {"trigger": "blocked", "situation": why, "kind": None}
                return _euklides_ask(c.st, job, {**e_, "situation": why},
                                     {"note": why, "proposal": ":stop", "then": "stop"}, None)
            to_euklides(c, job, "blocked", why, "step", back=WAITING.get(kind, "needs_worker"))
    if (j is None or j["status"] in run.TERMINAL) and proc.discard(c.dir, name):
        run.event(c.st, f"forgot {_clean(name)}, its result could not be archived; if it was still running, "
                        "it was not signalled")


def _charge(st: dict, name: str, r: dict) -> None:
    """Spent and gone whether or not anything still waits for the answer. What belongs to no job is the run's."""
    usd = _usd(r.get("usd"))
    j = st["jobs"].get(name.rpartition(".")[0])
    if j is not None:
        j["usd"] = round(j["usd"] + usd, 4)
    else:
        st["other_usd"] = round(st.get("other_usd", 0) + usd, 4)
    try:
        caps.record(f"{st['repo']}/{st['slug']}", name, usd)
    except OSError as e:
        run.event(st, f"the spending ledger cannot be written: {e}")


def _intervene_actions(j: dict) -> list[str]:
    out = ["stop_block"]
    if not j.get("waited"):
        out.append("wait")
    if j.get("steers", 0) < MAX_STEERS:
        out.append("steer")
    if j["tier"] in claude.NEXT_TIER:
        out.append("stop_escalate")
    return out


def _steer(st: dict, job: str, text: str) -> None:
    j = st["jobs"][job]
    j["steering"] = _clean(text)[:4000]
    proc.stop(paths.run_dir(st["repo"], st["slug"]), f"{job}.worker", STEERED)


def on_intervene(c: Ctx, job: str, r: dict, slept: bool = False) -> None:
    j = c.st["jobs"][job]
    j.pop("intervening", None)
    if (j["status"] != "working" or (r.get("meta") or {}).get("attempt") != j["attempts"]
            or any(k in j for k in HANDLING)):
        return run.event(c.st, f"{job}: ignored the intervene tick: the worker it looked at is gone or being handled")
    allowed = _intervene_actions(j)
    action, note = _tick_answer(r)
    if not isinstance(action, str) or action not in allowed:
        if slept:  # looked at with the clocks of before the sleep: watch() looks again with the moved ones
            j["anomaly"] = None
            return run.event(c.st, f"{job}: the intervene tick failed after the machine slept; the worker is looked at again")
        if ((_learned_nothing(r) or r.get("spoke") is False) and _resumes_left(j, paths.load_workspace()["limits"])
                and not proc.stop_requested(c.dir, f"{job}.worker")):
            # the tick got no answer from the model either: most likely the API is down, and the worker with it
            return _stop_api_hung(c, job, j.get("anomaly") or "the intervene tick got no answer from the model")
        action = next(a for a in ("wait", "stop_escalate", "stop_block") if a in allowed)
        note = f"the intervene tick gave no usable answer; the runner chose {action}: {j.get('anomaly')}"
    note = _clean(str(note or j.get("anomaly") or action))
    run.event(c.st, f"{job} intervene: {action} — {note}")
    if action == "wait":
        now = time.time()
        j.update(waited=True, anomaly=None, watch_from=now, token_factor=j["token_factor"] * 1.5,
                 soft_deadline=max(j["soft_deadline"], now) + claude.TIER_MINUTES[j["tier"]] * 30)
    elif action == "steer":
        j["steers"] = j.get("steers", 0) + 1
        _steer(c.st, job, note)
    else:
        j["stop_then"] = {"action": action, "note": note}
        proc.stop(c.dir, f"{job}.worker", INTERVENED)


def _on_receipt(c: Ctx, name: str, r: dict) -> None:
    _charge(c.st, name, r)
    if name == DECISIONS:
        slept = c.st.pop("slept_through", None)
        if c.st["closing"] == "decisions":
            text = _clean(r["result"] or "").strip()
            if slept and not (_clean_exit(r) and text.startswith("# Decisions")):
                c.st["closing"] = None  # maybe_close starts it again; this one was not paid for in vain
                c.st["decisions_ticks"] = max(0, c.st.get("decisions_ticks", 0) - 1)
                return run.event(c.st, "the decisions tick failed after the machine slept; it starts again")
            try:
                on_decisions(c, r)
            except Exception as e:  # the receipt is gone and the tick is paid: publish takes it from here
                run.event(c.st, f"the decisions tick's answer could not be kept: {type(e).__name__}: {_clean(str(e))}")
                c.st["closing"] = "publish"
        else:
            run.event(c.st, f"ignored the result of {name}: nothing was waiting for it")
        return
    if name == RUN_EUKLIDES:
        try:
            return on_run_euklides(c, r)
        except Exception as e:  # the receipt is gone: never leave the run holding for it without a question
            run.event(c.st, f"runner error after {name}: {type(e).__name__}: {_clean(str(e))}")
            spent, _, budget = _run_budget(c)
            c.st["cap_hold"] = True
            return _ask_cap_run(c.st, spent, budget) if spent > budget else None
    if name.startswith(REFRESH_PREFIX):
        try:
            return on_refresh(c, name, r)
        except Exception as e:  # the receipt is gone: never leave the refresh waiting for it
            live = _refresh_of(c.st, name)
            if live is not None:
                _finish_refresh(c.st, live, "failed",
                                f"runner error after {_clean(name)}: {type(e).__name__}: {_clean(str(e))}")
            return
    if name.startswith(FIX_PREFIX):
        try:
            return on_fix(c, name, r)
        except Exception as e:  # the receipt is gone: never leave the fix waiting for it
            f = _fix_of(c.st, name)
            if f is not None and f["status"] not in FIX_DONE:
                _fix_failed(c, f, f"runner error after {name}: {type(e).__name__}: {_clean(str(e))}")
            return
    job, _, kind = name.rpartition(".")
    j = c.st["jobs"].get(job)
    slept = j is not None and _slept_through(j, kind)
    if r.get("survivors"):
        run.alert(c.st, f"{_clean(job)}: processes of its {_clean(kind)} outlived it ({r['reason'] or 'it ended'}): "
                        "look for leftovers before they touch its worktree")
    if j is not None and r["reason"] == proc.STOPPED_BY_MARCO and j["status"] not in run.TERMINAL:
        block(c, job, proc.STOPPED_BY_MARCO)  # before the check below: whatever the job waits for, it ends here
        return
    if j is not None and kind == "intervene":  # never the job's main process: it does not move the job's status
        try:
            on_intervene(c, job, r, slept)
        except Exception as e:
            j.pop("intervening", None)
            run.event(c.st, f"{job}: the intervene tick's answer failed: {type(e).__name__}: {_clean(str(e))}")
        return
    if j is None or kind not in HANDLERS or RUNNING.get(j["status"]) != kind:
        run.event(c.st, f"ignored the result of {_clean(name)}: nothing was waiting for it")
        return
    if slept and not _clean_success(kind, r) and r["reason"] not in OURS and not any(k in j for k in HANDLING):
        return _free_retry(c, job, kind, r)
    if kind in MODEL_KINDS:
        if _learned_nothing(r) and not j.get("attaching"):
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
        j.pop("holder_tries", None)
        return True
    if j.get("not_before", 0) <= time.time():
        _passing(c, job, "holder_tries", what, j["status"])
    return False


def _watch_from(j: dict, start: float) -> None:
    """A new worker's limits, counted from when its process started."""
    j.update(soft_deadline=start + claude.TIER_MINUTES[j["tier"]] * 60, watch_from=start, token_factor=1, waited=False,
             anomaly=None)


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
    _write_grants(c.st, job)
    env = claude.runner_env("worker", repo_name=c.st["repo"], origin_url=gitops.origin_url(c.repo), worktree=wt, area=json.dumps(run.area(c.st, job, mj)),
                            report=report, gate=mj["gate"], run_dir=c.dir, job=job, grants_file=grants_file(c.st, job),
                            **({"companions": "0"} if mj.get("companions") is False else {}))
    sid = j["session"] or str(uuid.uuid4())  # the worker's id before it says so: a kill may come first
    cmd = claude.agent_cmd("worker", claude.MODELS[j["tier"]], prompt, session=sid, resume=resume, usd=mj["budget"]["usd"])
    proc.start(c.dir, f"{job}.worker", cmd, cwd=wt, env=env, minutes=claude.TIER_MINUTES[j["tier"]] * claude.HARD_FACTOR, pre=pre,
               meta={"session": sid, "tier": j["tier"], "attempt": j["attempts"] + 1})
    j.update(status="working", attempts=j["attempts"] + 1, error=None)
    _watch_from(j, time.time())
    run.event(c.st, f"{job}: worker {j['tier']} attempt {j['attempts']}{' (resumed)' if resume else ''}")
    return True


def push_job(c: Ctx, job: str) -> None:
    """The worker's result is saved and paid for, so a failed push retries through _error. Only a branch
    on origin that this head does not descend from can never heal: Marco decides what to do with it. Unless
    Euklides rewrote the branch (`rewrite_ok`): the old head is kept on ark/backup/<slug>-<job>-<sha8>, and the
    new one replaces it with a lease on the old."""
    j, branch = c.st["jobs"][job], c.branch(job)
    try:
        gitops.push_ref(c.repo, j["head"], branch)
    except gitops.GitError:
        remote = gitops.remote_sha(c.repo, branch)
        if remote and remote != j["head"] and not gitops.is_ancestor(c.repo, remote, j["head"]):
            if j.get("rewrite_ok"):
                return _push_rewrite(c, job, branch, remote)
            return _passing(c, job, "push_tries",
                            f"non-fast-forward: {branch} on origin holds {remote[:8]}, which this head does "
                            "not descend from: the worker amended or rebased, or someone else pushed there. "
                            "Delete that branch or look at it", "needs_push")
        raise
    _pushed(c, job)


def _pushed(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    j.pop("push_tries", None)
    j.pop("rewrite_ok", None)
    j["status"] = "needs_gate"
    run.event(c.st, f"{job}: head {j['head'][:8]} pushed")


def _push_rewrite(c: Ctx, job: str, branch: str, remote: str) -> None:
    """Euklides' rebuilt head over the one on origin: the old head first goes to a backup branch."""
    j = c.st["jobs"][job]
    backup = f"ark/backup/{c.st['slug']}-{job}-{remote[:8]}"
    if gitops.remote_sha(c.repo, backup) != remote:
        gitops.fetch(c.repo)  # the old head may only be on origin
        gitops.push_ref(c.repo, remote, backup)
    gitops.push_ref_lease(c.repo, j["head"], branch, remote)
    run.alert(c.st, f"{job}: Euklides rewrote {branch}; its old head {remote[:8]} is kept on {backup}")
    _pushed(c, job)


def start_gate(c: Ctx, job: str) -> None:
    j, mj = c.st["jobs"][job], c.mjobs[job]
    env = gate_env(c, "gate")  # it runs tests a worker wrote
    gate = j.get("accept_gate") or mj["gate"]  # Marco's ok to an `accept`: the replacement, for this head only
    proc.start(c.dir, f"{job}.gate", ["sh", "-c", gate], cwd=c.wt(job), env=env,
               minutes=mj["budget"]["minutes"], meta={"head": j["head"]})
    if j.get("accept_gate"):
        run.alert(c.st, f"{job}: the runner runs the replacement gate `{_clean(gate)[:200]}` on head "
                        f"{j['head'][:8]}, by Marco's ok")
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
    env = claude.runner_env("reviewer", repo_name=c.st["repo"], origin_url=gitops.origin_url(c.repo), worktree=c.wt(job),
                            area=json.dumps(run.area(c.st, job, mj)), job=job, run_dir=c.dir)
    proc.start(c.dir, f"{job}.review", cmd, cwd=c.wt(job), env=env, minutes=30, meta={"head": j["head"]})
    j["status"] = "reviewing"


def start_integrate(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    if j["pr"] and merge.confirmed(c.st, job, c.forge, c.repo):  # a crash lost it: a red check now would pay a worker
        return _merged(c, job)
    cmd = [str(paths.code_dir() / "bin" / "ark-integrate"), f"{c.st['repo']}/{c.st['slug']}", job, j["head"]]
    env = gate_env(c, "integrate")  # it runs tests a worker wrote
    # Above the check's own timeout, or a slow gate dies here with no result instead of red with its reason.
    proc.start(c.dir, f"{job}.integrate", cmd, cwd=paths.code_dir(), env=env,
               minutes=2 * integrate.GATE_TIMEOUT / 60 + 30, meta={"head": j["head"]})
    j["status"] = "integrating"


def _merged(c: Ctx, job: str) -> None:
    c.st["jobs"][job]["status"] = "merged"
    _raise_outcome(c.st["jobs"][job], "merged")
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
        return block(c, job, f"ark-merge refused: {e}", critical=True)
    _merged(c, job)


def _dependents(c: Ctx, job: str) -> list[str]:
    return [m["id"] for m in c.plan.manifest["jobs"] if job in (m.get("after") or [])]


def regate_open(j: dict, e: dict) -> bool:
    """A red gate on a head that passed a gate or a CLEAN review, not yet re-run for this gate and head."""
    red = e.get("red")
    return bool(red) and red.get("head") in (j.get("green_heads") or []) \
        and f"{red['kind']}:{red['head']}" not in (j.get("regated") or [])


def start_euklides(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    e, mj = j["euklides"], c.mjobs[job]
    cfg = euklides.settings()
    e["allowed"] = euklides.allowed(e["trigger"], j, mj["budget"]["usd"], cfg["budget_margin"],  # fixed now: the
                                    kind=e.get("kind"), base_red=bool(e.get("base_red")),  # answer is checked
                                    fixes_used=c.st.get("fix_base_count", 0),  # against it
                                    regate=regate_open(j, e),
                                    stalled=e["trigger"] == "failure" and progress.stalled(j))
    wt = c.wt(job)
    commits, changed = _branch_lists(wt, f"origin/{c.base}")
    prompt = brief.euklides_prompt(c.st, job, j, mj, c.plan, trigger=e["trigger"], situation=e["situation"],
                                   base=c.base, commits=commits, changed=changed,
                                   allowed=e["allowed"], report=c.read_report(job), dependents=_dependents(c, job),
                                   worktree=wt, log=c.dir / f"{job}.worker.log", lessons=euklides.lessons(),
                                   earlier=[d for d in euklides.decisions()
                                            if str(d.get("id", "")).startswith(f"{c.st['repo']}/{c.st['slug']}/{job}/")][-10:],
                                   base_check=("red on " + c.base + " without the job too") if e.get("base_red") else None,
                                   usd_raised=j.get("usd_raised", 0),
                                   margin_left=euklides.margin_left(j, mj["budget"]["usd"], cfg["budget_margin"]))
    cmd = claude.agent_cmd("euklides", cfg["model"], prompt, session=str(uuid.uuid4()), usd=cfg["usd_per_call"],
                           tools=cfg["tools"], denied_tools=cfg["denied_tools"])
    # Bash and edits in the job's worktree: the push layer and merge-guard leave pushing and PRs to the runner
    env = claude.runner_env("euklides", repo_name=c.st["repo"], origin_url=gitops.origin_url(c.repo))
    proc.start(c.dir, f"{job}.euklides", cmd, cwd=wt if wt.is_dir() else ticks_dir(), env=env,
               minutes=cfg["minutes_per_call"])
    j["status"] = "euklides"


def _branch_lists(wt: Path, base: str) -> tuple[list, list]:
    """The job's commits and changed files against the base, for Euklides' prompt; empty without a worktree."""
    if not wt.is_dir():
        return [], []
    try:
        return gitops.branch_commits(wt, base), gitops.branch_changes(wt, base)
    except gitops.GitError:
        return [], []


# ---- closing ----------------------------------------------------------------------------------

STEP_ERRORS = (gitops.GitError, forge_mod.ForgeError)  # offline, a refused push: healed by trying again


def _reports(c: Ctx) -> dict[str, str]:
    return {jid: c.read_report(jid) or "" for jid in c.st["jobs"]}


def maybe_close(c: Ctx) -> None:
    st = c.st
    live = [j for j in st["jobs"].values() if j["status"] not in run.TERMINAL]
    if live and all(j["status"] == "waiting_dep" for j in live) and not proc.active(c.dir) \
            and not any(st["jobs"][j["waiting_on"]]["status"] in ("waiting_answer", "attached") for j in live):
        for job, j in st["jobs"].items():  # nothing can move them now: the run ends
            if j["status"] == "waiting_dep":
                _block(st, job, f"a job it depends on is blocked: {j['waiting_on']}")
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
        proc.start(c.dir, DECISIONS, cmd, cwd=ticks_dir(), env=claude.runner_env("tick", repo_name=c.st["repo"]), minutes=15)
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


def _closed(st: dict) -> None:
    """The run is closed: stamped with when, which the sweep counts its abandon window from."""
    st.update(closing="done", closed_at=run.now())


def _stop_closing(c: Ctx, why: str) -> None:
    """A finished run is never entered again, so this tells Marco what is left to do by hand."""
    st = c.st
    _forget_retries(st)
    st.update(status="blocked")
    _closed(st)
    _queue_flaky(c)
    _close_report(c, record=True)
    file = f"The decisions file is on ark/{st['slug']}" if st.get("decisions_sha") else \
        f"The decisions file was not committed; its text, if it could be written, is at {c.dir / 'decisions.md'}"
    run.alert(st, f"run blocked: {why}. {file}, and no final PR was opened: the rest is by hand" + _report_note(st),
              critical=True)


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


def _queue_flaky(c: Ctx) -> None:
    """One coach queue item per run with flaky gates. A failed write never stops the close: the report says it."""
    st = c.st
    if not st.get("flaky") or st.get("flaky_queued") or st.get("queue_error"):
        return
    try:
        f = coachqueue.add_flaky(c.repo, st)
    except OSError as e:
        st["queue_error"] = _clean(f"{type(e).__name__}: {e}")[:300]
        return run.event(st, f"the coach queue item for the flaky gates was not written: {st['queue_error']}")
    st["flaky_queued"] = True
    run.event(st, f"flaky gates queued in {f}")


def _close_report(c: Ctx, *, record: bool) -> str:
    """The run's report, and with `record` its history. Never in the way of the close: a failure is an event.
    Returns the summary for the final PR body ("" if there is none)."""
    try:
        out = reporting.generate(c.st, c.mjobs, c.repo, record=record)
    except Exception as e:
        run.event(c.st, f"report not written: {_clean(f'{type(e).__name__}: {e}')[:300]}")
        return ""
    c.st["report"] = str(out["path"])
    return out["markdown"]


def _report_note(st: dict) -> str:
    return f" · report: {st['report']}" if st.get("report") else ""


def publish(c: Ctx) -> None:
    """Commit docs/decisions/<slug>.md on ark/<slug>, open the final PR, finish. Retried within _closing_failed."""
    st = c.st
    try:
        if not st.get("decisions_sha"):
            st["decisions_sha"] = gitops.commit_file(
                c.repo, c.base, f"docs/decisions/{st['slug']}.md", _decisions_text(c),
                f"docs: decisions for {st['slug']}", paths.worktrees_dir() / st["repo"] / st["slug"] / "_decisions")
            run.event(st, f"decisions file committed on ark/{st['slug']}")
        _queue_flaky(c)
        merged = any(j["status"] == "merged" for j in st["jobs"].values())
        if merged and not st["final_pr"]:
            md = _close_report(c, record=False)
            st["final_pr"] = c.forge.create(c.repo, base=st["default_branch"], head=c.base,
                                            title=c.plan.manifest["final_pr_title"], body=brief.final_pr_body(st, md))
    except gitops.PathRefused as e:  # the branch itself is the problem: no retry heals it
        return _stop_closing(c, _clean(f"{type(e).__name__}: {e}"))
    except Exception as e:
        return _closing_failed(c, e)
    _forget_retries(st)
    all_merged = all(j["status"] == "merged" for j in st["jobs"].values())
    st.update(status="done" if all_merged else "blocked")
    _closed(st)
    _close_report(c, record=True)
    pr = f"final PR {st['final_pr']['url']}" if st["final_pr"] else "no job merged, no final PR"
    run.alert(st, f"run {st['status']}: {pr}{_report_note(st)}", critical=True)


# ---- scheduling -------------------------------------------------------------------------------

class Slots:
    """Writers (workers alive) per repo and in total, and gates (all of them, and the heavy ones), across every
    run. Integration gates and base fix gates run the whole suite: they always count as heavy."""

    def __init__(self, states, limits: dict):
        self.limits, self.writers, self.gates, self.heavy = limits, {}, 0, 0
        self.quiet = False  # a quiet re-run (regate) waits or runs: nothing new starts
        names = {accounts.account_of(st["repo"]) for st in states} | {accounts.PERSONAL}
        self.capped_accounts = {a for a in names if caps.capped(a, limits)}
        for st in states:
            for j in st["jobs"].values():
                if j["status"] == "working":
                    self.writers[st["repo"]] = self.writers.get(st["repo"], 0) + 1
                if j["status"] in ("gating", "integrating"):
                    self.gates += 1
                    if j["status"] == "integrating" or j.get("heavy"):
                        self.heavy += 1
            for f in st.get("fixes") or []:
                if f["status"] == "working":  # a base-fix worker writes too
                    self.writers[st["repo"]] = self.writers.get(st["repo"], 0) + 1
                if f["status"] == "gating":  # its gate is the integration gate
                    self.gates += 1
                    self.heavy += 1
            r = st.get("refresh")
            if r and r.get("status") in ("checking", "gating"):
                self.gates += 1
                if _plan_heavy(st):
                    self.heavy += 1
            if r and r.get("status") == "working":  # a refresh worker writes too
                self.writers[st["repo"]] = self.writers.get(st["repo"], 0) + 1
        self.quiet = any(j.get("regate") and j["status"] in REGATE_STATUSES
                         for st in states for j in st["jobs"].values())

    def capped(self, repo: str) -> bool:
        return accounts.account_of(repo) in self.capped_accounts

    def writer(self, repo: str) -> bool:
        if self.quiet:
            return False
        if self.writers.get(repo, 0) >= self.limits["writers_per_repo"] or \
                sum(self.writers.values()) >= self.limits["writers_total"]:
            return False
        self.writers[repo] = self.writers.get(repo, 0) + 1
        return True

    def release_writer(self, repo: str) -> None:
        self.writers[repo] -= 1

    def gate(self, heavy: bool, st: dict | None = None, key: str = "") -> bool:
        """May a gate start now? `st` and `key` (the run, and what gates in it) turn the load brake on."""
        if self.quiet:
            return False
        if st is not None and self._load_hold(st, key):
            return False
        if self.gates >= self.limits.get("gates_total", 2) or (heavy and self.heavy >= self.limits["heavy_gates"]):
            return False
        self.gates += 1
        if heavy:
            self.heavy += 1
        if st is not None:
            _load_wait.pop((st["repo"], st["slug"], key), None)  # it starts: the next one waits afresh
        return True

    def regate(self) -> bool:
        """A quiet re-run takes the machine: it starts only when no gate of any kind runs."""
        if self.gates:
            return False
        self.gates += 1
        self.heavy += 1
        return True

    def _load_hold(self, st: dict, key: str) -> bool:
        """The load brake: True while the machine is busier than `load_max`, for at most `load_wait_max`
        seconds (monotonic, so a sleep does not count). One event when the wait begins, one when it gives up."""
        top = self.limits.get("load_max") or 0
        k = (st["repo"], st["slug"], key)
        try:
            load = os.getloadavg()[0]
        except OSError:
            load = 0.0
        if not top or load <= top:
            _load_wait.pop(k, None)
            return False
        now = time.monotonic()
        w = _load_wait.get(k)
        if w is None:
            _load_wait[k] = [now, False]
            run.event(st, f"{key}: a gate waits for the machine (load {load:.1f} above {top})")
            return True
        if w[1]:
            return False
        if now - w[0] >= self.limits.get("load_wait_max", 900):
            w[1] = True
            run.event(st, f"{key}: the gate starts after waiting {int(now - w[0])}s for the load (now {load:.1f})")
            return False
        return True


_load_wait: dict = {}  # (repo, slug, key) -> [monotonic start of the wait, gave up waiting]


def gate_env(c: Ctx, role: str) -> dict:
    """The environment of a gate process. ARK_TEST_WORKERS (limits.gate_workers) is always set, so a plan's gate can
    pass `--maxWorkers=$ARK_TEST_WORKERS`; vitest and pytest-xdist read their own variables, set when the
    environment does not already have them. A flag in the gate's command line wins over any of these."""
    env = claude.runner_env(role, repo_name=c.st["repo"], origin_url=gitops.origin_url(c.repo))
    n = str(max(1, int(paths.load_workspace()["limits"].get("gate_workers") or 4)))
    env["ARK_TEST_WORKERS"] = n
    env.setdefault("VITEST_MAX_WORKERS", n)  # documented by vitest
    env.setdefault("PYTEST_XDIST_AUTO_NUM_WORKERS", n)  # what `-n auto` means in pytest-xdist; jest has no variable
    return env


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
        if not all(d == "merged" for d in deps):
            return None
    if s in ("pending", "needs_worker"):
        if not c.st.get("base_green", True):  # no work on a base nobody saw green; _migrate sets the key
            return None
        if slots.writer(repo):
            return lambda: _start_writer(c, job, slots)
    elif s in ("needs_gate", "needs_integrate"):
        start = start_gate if s == "needs_gate" else start_integrate
        rg = j.get("regate")
        if rg and time.time() > rg["until"]:
            return lambda: _regate_ask(c, job, "the quiet re-run waited past the gate's deadline for the machine")
        if rg and slots.regate():
            return lambda: _start_regate(c, job, start)
        if not rg and slots.gate(j["heavy"] or s == "needs_integrate", c.st, job):
            return lambda: start(c, job)
    else:
        step_ = {"needs_push": push_job, "needs_pr": ensure_pr, "needs_review": start_review, "needs_merge": do_merge,
                 "needs_euklides": start_euklides}.get(s)
        if step_:
            return lambda: step_(c, job)
    return None


# a job with one of these is already being dealt with
HANDLING = ("intervening", "stop_then", "steering", "attaching", "api_hung")


def _quiet(j: dict, log: Path, now: float) -> float:
    """Seconds since the worker's log last moved, counted from its start or its wake at the earliest."""
    return now - max(_mtime(log) / 1e9, j["watch_from"], j.get("woke_at", 0))


def _anomaly(j: dict, log: Path, now: float, limits: dict | None = None) -> str | None:
    """What looks wrong with this worker, or None (spec §6)."""
    stall = (limits or {}).get("stall_minutes", claude.STALL_MINUTES)
    if now > j["soft_deadline"]:
        return f"over its time for tier {j['tier']}"
    if _quiet(j, log, now) > stall * 60:
        return f"no output for over {stall:g} minutes"
    tokens = usage.scan(log)["tokens"]
    if tokens > claude.TIER_TOKENS[j["tier"]] * j["token_factor"]:
        return f"{tokens} tokens, over the limit for tier {j['tier']}"
    return None


def start_intervene(c: Ctx, job: str, why: str) -> None:
    j = c.st["jobs"][job]
    log = c.dir / f"{job}.worker.log"
    prompt = brief.intervene_prompt(c.st, job, c.mjobs[job]["pr_title"], why, usage.scan(log)["last"], log,
                                    _intervene_actions(j))
    cmd = claude.agent_cmd("tick", claude.TICK_MODEL["intervene"], prompt, session=str(uuid.uuid4()), usd=1)
    proc.start(c.dir, f"{job}.intervene", cmd, cwd=ticks_dir(), env=claude.runner_env("tick", repo_name=c.st["repo"]), minutes=15,
               meta={"attempt": j["attempts"]})
    j.update(intervening=True, anomaly=why)
    run.event(c.st, f"{job} looks wrong: {why}")


def watch(c: Ctx) -> None:
    now, ended, limits = time.time(), proc.finished(c.dir), paths.load_workspace()["limits"]
    for job, j in c.st["jobs"].items():
        name = f"{job}.worker"
        if (j["status"] != "working" or "soft_deadline" not in j or any(k in j for k in HANDLING)
                or name in ended or proc.stop_requested(c.dir, name)):
            continue
        log = c.dir / f"{name}.log"
        error = usage.scan(log)["api_error"]
        if error and _quiet(j, log, now) > limits["api_quiet_minutes"] * 60 and _resumes_left(j, limits):
            _stop_api_hung(c, job, error)
            continue
        why = _anomaly(j, log, now, limits)
        if why:
            try:
                start_intervene(c, job, why)
            except Exception as e:  # the next cycle looks again
                run.event(c.st, f"{job}: the intervene tick did not start: {type(e).__name__}: {_clean(str(e))}")


def _stuck_dep(c: Ctx, job: str) -> str | None:
    return next((d for d in c.mjobs[job].get("after") or [] if c.st["jobs"][d]["status"] in WAITS_ON), None)


def _hold_dependents(c: Ctx) -> None:
    """A job whose dependency is blocked or waits for Marco waits too, and starts again when it moves."""
    for job, j in c.st["jobs"].items():
        if j["status"] not in ("pending", "waiting_dep"):
            continue
        dep = _stuck_dep(c, job)
        if dep and j["status"] == "pending":
            j.update(status="waiting_dep", waiting_on=dep)
            run.event(c.st, f"{job} waits: {dep} is {c.st['jobs'][dep]['status']}")
        elif dep:
            j["waiting_on"] = dep
        elif j["status"] == "waiting_dep":
            j["status"] = "pending"
            j.pop("waiting_on", None)
            run.event(c.st, f"{job}: nothing it depends on is stuck any more")


def schedule(c: Ctx, slots: Slots) -> None:
    _hold_dependents(c)
    _move_fixes(c, slots)
    _move_refresh(c, slots)
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
            j.pop("step_tries", None)


def _gone_worker(st: dict, job: str, j: dict) -> bool:
    """The worker was being stopped on purpose, and a crash lost its receipt: what was decided still happens."""
    steering, then = j.pop("steering", None), j.pop("stop_then", None)
    if then:
        _after_stop(st, job, then)
    elif steering:
        j.update(note=steering, status="needs_worker")
    elif j.pop("attaching", None):
        j["status"] = "attached"
    elif hung := j.pop("api_hung", None):
        j.update(note=f"{brief.RESUME['api']}\n\nWhat the runner saw: {hung}", status="needs_worker")
    else:
        return False
    run.event(st, f"{job}: its worker is gone, as was decided")
    return True


def _reconcile_fixes(st: dict, d: Path) -> None:
    """A base fix whose process is gone with no receipt goes back one step; the second time it fails."""
    for f in st.get("fixes") or []:
        kind = FIX_RUNNING.get(f["status"])
        if kind is None or (d / f"{FIX_PREFIX}{f['n']}.{kind}.proc").exists():
            continue
        f["lost"] = f.get("lost", 0) + 1
        if f["lost"] >= 2:
            _fix_failed(None, f, f"its {kind} was lost twice without a result", st=st)
            continue
        f["status"] = FIX_WAITING_FOR[kind]
        run.event(st, f"base fix {f['n']}: its {kind} is gone without a result; going back to {f['status']}")


def reconcile(st: dict) -> None:
    """The state is saved at the end of a cycle and processes start and end during it, so a crash can
    leave them apart. A process the state does not know about is adopted, never started twice; a status
    waiting for a process that is gone goes back a step, or the job would wait forever."""
    if st["status"] in run.FINISHED:
        return
    d = paths.run_dir(st["repo"], st["slug"])
    _reconcile_fixes(st, d)
    _reconcile_refresh(st, d)
    if st.get("cap_euklides") and not (d / f"{RUN_EUKLIDES}.proc").exists():
        st.pop("cap_euklides")
        st.pop("cap_hold", None)  # the next cap check asks him again
        run.event(st, "the run's Euklides call is gone without a result; asking again")
    if st.get("closing") == "decisions" and not (d / f"{DECISIONS}.proc").exists():
        st["closing"] = None
        run.event(st, "the decisions tick is gone without a result; starting it again")
    for job, j in st["jobs"].items():
        s = j["status"]
        if (d / f"{job}.intervene.proc").exists():
            if not j.get("intervening"):
                j["intervening"] = True
                run.event(st, f"{job}: found its intervene tick running; the state had not recorded it")
        elif j.pop("intervening", None):
            run.event(st, f"{job}: its intervene tick is gone without a result")
        if s in RUNNING:
            kind = RUNNING[s]
            if not (d / f"{job}.{kind}.proc").exists():
                if proc.stop_requested(d, f"{job}.{kind}"):  # consumed, then a crash before the save
                    _block(st, job, proc.STOPPED_BY_MARCO)
                    continue
                if kind == "worker" and _gone_worker(st, job, j):
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
                    start = (proc._read(d / f"{job}.worker.proc") or {}).get("start")
                    _watch_from(j, start if type(start) in (int, float) and math.isfinite(start) else time.time())
                run.event(st, f"{job}: found its {kind} running; the state had not recorded it")


# ---- sleep --------------------------------------------------------------------------------------

def clocks() -> tuple[float, float]:
    """(wall, uptime). The uptime clock does not advance while the Mac sleeps; CLOCK_MONOTONIC does on macOS.
    Linux (CI) has no CLOCK_UPTIME_RAW, and there CLOCK_MONOTONIC already stops during suspend."""
    return time.time(), time.clock_gettime(getattr(time, "CLOCK_UPTIME_RAW", time.CLOCK_MONOTONIC))


def slept_between(before: tuple[float, float], after: tuple[float, float]) -> float:
    """Seconds asleep between two clocks() samples; 0 for a short jump or a wall clock set back."""
    slept = (after[0] - before[0]) - (after[1] - before[1])
    return slept if slept > SLEEP_MIN else 0


def _span(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}h{m % 60:02d}m"


def shift_run(st: dict, slept: float, woke_at: float) -> None:
    """Time asleep is no time worked: every limit moves by it, and whatever lived through it gets one free failure."""
    d = paths.run_dir(st["repo"], st["slug"])
    for j in st["jobs"].values():
        for k in CLOCK_KEYS:
            if j.get(k):
                j[k] += slept
        if j["status"] not in run.TERMINAL:
            j["woke_at"] = woke_at
    if st.get("closing_wait"):
        st["closing_wait"] += slept
    for name in proc.active(d):
        if name == DECISIONS:
            st["slept_through"] = woke_at
            continue
        job, _, kind = name.rpartition(".")
        if job in st["jobs"]:
            st["jobs"][job].setdefault("slept_through", {})[kind] = woke_at
    for line in proc.shift_deadlines(d, slept):
        run.event(st, f"a deadline could not be moved after the sleep: {_clean(line)}")
    run.event(st, f"the machine slept {_span(slept)}: deadlines moved by that much")


def _net_up() -> bool:
    for host, port in NET_HOSTS:
        try:
            socket.create_connection((host, port), timeout=3).close()
        except OSError:
            return False
    return True


def net_hold() -> str:
    """After a wake: "wait" while the network is not back, "gave_up" once when NET_WAIT_MAX ran out, "" otherwise."""
    global _net_wait_until
    if not _net_wait_until:
        return ""
    if time.time() >= _net_wait_until:
        _net_wait_until = 0.0
        return "gave_up"
    if _net_up():
        _net_wait_until = 0.0
        return ""
    return "wait"


def on_wake(slept: float, woke_at: float) -> list[str]:
    """Called by the poll before the cycle when the machine slept. Returns lines to print."""
    global _net_wait_until
    _net_wait_until = woke_at + NET_WAIT_MAX
    out = []
    for a in approvals():
        try:
            st = _load(a["repo"], a["slug"])
        except Exception:  # the cycle says so, once
            continue
        if not st or st["status"] in run.FINISHED:
            continue
        shift_run(st, slept, woke_at)
        try:
            run.save(st)
        except Exception as e:  # the free failure covers what this save loses
            out.append(f"{a['repo']}/{a['slug']}: the state could not be saved after the sleep: {type(e).__name__}: {e}")
    return out


def _apply_answer(st: dict, item: dict) -> None:
    qid, text = item.get("id"), item.get("text")
    q = questions.find(st, qid)
    if q is None or not isinstance(text, str):
        return run.event(st, f"an answer to {_clean(str(qid))} found no open question")
    problem = questions.check_answer(q, text)
    if problem:
        return run.alert(st, f"the answer to {q['id']} was refused: {problem}")
    answer = questions.resolve(st, q["id"], text)
    run.event(st, f"{q['id']} answered: {_clean(answer['answer'])[:300]}")
    ANSWERS[q["kind"]](st, q, answer["answer"])


def _answered_worker(st: dict, q: dict, text: str) -> None:
    job = q["job"]
    j = st["jobs"].get(job)
    if j is None or j["status"] != "waiting_answer":
        return run.event(st, f"{q['id']} was answered, but {_clean(str(job))} no longer waits for it")
    ask = j.pop("euklides_ask", None)
    if ask and ask.get("decision"):
        try:
            euklides.set_marco(ask["decision"], text)
        except OSError as err:
            run.event(st, f"{job}: the Euklides journal could not be written: {err}")
    choice = questions.ok_choice(text) if ask else None
    if choice == 1 and not ask.get("refuse_ok"):
        return _carry_out(st, job, q, ask)
    if choice == 2 and ask.get("alternative"):
        return _carry_out(st, job, q, _with_alternative(ask))
    if text == questions.STOP_JOB or text.startswith(questions.STOP_JOB + " "):
        return _block(st, job, f"stopped by Marco at question {q['id']}: {text}"[:600])
    if ask:
        return _go_on(st, job, ask["ctx"], text)
    _resume_with_answer(st, job, text)  # a question asked before Euklides


def _with_alternative(ask: dict) -> dict:
    """Euklides' ask with its alternative in place of the first option: its `then` and parameters, and its label
    as the proposal (what a go_on tells the worker). The note, decision and situation stay."""
    alt = ask["alternative"]
    out = {k: v for k, v in ask.items() if k not in ("then", "files", "usd", "gate", "alternative", "refuse_ok")}
    return {**out, **{k: v for k, v in alt.items() if k != "label"}, "proposal": alt["label"]}


def _effect_ok(st: dict, job: str, then: str | None, ask: dict, before: dict) -> str | None:
    """Why Marco's ok to `then` left no trace, or None when its effect is there: widen puts the files in the area,
    raise grows usd_raised, fix_base leaves a live fix with the job waiting on it, escalate changes the tier (or the
    job was at the top), accept queues the job's head for the replacement gate. Other thens have no check."""
    j = st["jobs"][job]
    if then == "widen":
        area = run.area(st, job, {"area": []})  # the grants; the plan's own globs only make a widen redundant
        missing = [f for f in ask.get("files") or [] if not match_any(f, area)]
        return f"{', '.join(missing)} did not join the job's area" if missing else None
    if then == "raise":
        return None if j.get("usd_raised", 0) > before["usd_raised"] + 1e-9 else "the budget did not grow"
    if then == "fix_base":
        live = [f for f in st.get("fixes") or [] if f["status"] not in FIX_DONE and job in f["waiting"]]
        return None if live else "no base fix is running for the job"
    if then == "escalate":
        return None if j["tier"] != before["tier"] or before["tier"] not in claude.NEXT_TIER else "the tier did not change"
    if then == "accept":
        if j["status"] == "needs_push" and j.get("accept_gate") == ask.get("gate") and j.get("head"):
            return None
        return before.get("accept_why") or "the head was not queued for the replacement gate"
    return None


def _carry_out(st: dict, job: str, q: dict, ask: dict) -> None:
    """Marco's ok to Euklides' ask, checked: the runner carries it out, and when the effect is not there (nothing
    was widened, raised, fixed, moved or queued) it says so and asks again, instead of resuming a worker on an
    answer it could not carry out. The way out of such a question is `stop` or a plan edit and a new approval."""
    j, then = st["jobs"][job], ask.get("then")
    before = {"usd_raised": j.get("usd_raised", 0), "tier": j["tier"]}
    snapshot = copy.deepcopy(j)
    _run_then(st, job, q, ask, before)
    why = _effect_ok(st, job, then, ask, before)
    if why is None:
        return
    st["jobs"][job] = snapshot  # what the half-done ok changed on the job is undone
    msg = f"Marco's ok to {then} was not carried out: {why}"
    run.alert(st, f"{job}: {msg}")
    _euklides_ask(st, job, ask["ctx"], {"note": f"{msg}. The way out is a plan edit and a new approval, or `stop`; "
                                                "the runner cannot do it for the job.",
                                        "proposal": ":stop, or say what the job should do", "then": "stop"}, None)


def _run_then(st: dict, job: str, q: dict, ask: dict, before: dict) -> None:
    """Marco's ok to Euklides' ask: the runner carries out its `then`, with Marco's authority. A state saved
    before `then` existed holds `grant` or `raise_usd` instead, or neither (ok took the proposal's text)."""
    j, e = st["jobs"][job], ask["ctx"]
    then, files, usd = ask.get("then"), ask.get("files"), ask.get("usd")
    if then is None:
        if ask.get("grant"):
            then, files = "widen", ask["grant"]
        elif ask.get("raise_usd"):
            then, usd = "raise", ask["raise_usd"]
        else:
            then = "go_on"
    note = ask.get("note") or ask.get("proposal") or "go on"
    if then == "widen":
        if e.get("kind") == "area":
            return _grant(st, job, files, ask.get("decision"), by="Marco")
        _add_grant(st, job, files, ask.get("decision"), by="Marco")
        return _go_on(st, job, e, f"{note}\n\nMarco granted {', '.join(files)} to this job's area.")
    if then == "raise":
        planned = _budget_usd(st, job) or 0.0  # unreadable plan: the amendment still shows what was raised
        before = round(planned + j.get("usd_raised", 0), 4)
        j["usd_raised"] = round(j.get("usd_raised", 0) + usd, 4)
        j["marco_raised"] = round(j.get("marco_raised", 0) + usd, 4)  # never against Euklides' margin
        amend.add(st, job, "budget", float(before), round(planned + j["usd_raised"], 4), by="Marco",
                  decision=ask.get("decision"), why=f"question {q['id']}: {ask.get('note') or ''}", undo="job")
        run.event(st, f"{job}: Marco raised its budget by ${usd:.2f}")
        return _go_on(st, job, e, ask.get("note") or "go on: Marco raised the budget")
    if then == "fix_base":
        return _start_base_fix(st, job, e, {"note": note, "proposal": ask.get("proposal")}, ask.get("decision"),
                               by="Marco")
    if then == "escalate":
        if j["tier"] in claude.NEXT_TIER:
            _escalate(j)
            run.event(st, f"{job}: Marco moved it to {j['tier']}")
        else:
            run.event(st, f"{job}: stays at {j['tier']}, the top tier; it goes on there")
        return _go_on(st, job, e, note)
    if then == "accept":
        wt = _wt(st, job)
        if not ask.get("gate"):
            before["accept_why"] = "the ask names no gate"
        elif not wt.is_dir() or not j.get("head"):
            before["accept_why"] = "the job has no committed head in a worktree"
        elif not gitops.is_clean(wt):
            before["accept_why"] = "the job's worktree has uncommitted changes, so its head is not all there is"
        else:
            j.update(accept_gate=ask["gate"], head=gitops.rev(wt, "HEAD"), status="needs_push")
            amend.add(st, job, "gate", "the plan's gate", ask["gate"], by="Marco", decision=ask.get("decision"),
                      why=f"question {q['id']}, for head {j['head'][:8]} only", undo="job")
            for k in ("question", "not_before", "push_tries", "step_tries", "rewrite_ok"):
                j.pop(k, None)
            run.event(st, f"{job}: Marco accepted head {j['head'][:8]} on the replacement gate "
                          f"`{_clean(ask['gate'])[:200]}`; no worker")
        return
    if then == "stop":
        return _block(st, job, f"stopped by Marco at question {q['id']} (ok to Euklides): {note}"[:600])
    text = ask.get("proposal") or "ok"
    if text == questions.STOP_JOB or text.startswith(questions.STOP_JOB + " "):
        return _block(st, job, f"stopped by Marco at question {q['id']}: {text}"[:600])
    _go_on(st, job, e, text)


def _apply_stop(st: dict, item: dict) -> None:
    """`ark stop` of a job with no process when he typed it. One may have started since: it stops the usual way."""
    job = item.get("job")
    j = st["jobs"].get(job)
    if j is None or j["status"] in run.TERMINAL:
        return run.event(st, f"a stop for {_clean(str(job))} found nothing to stop")
    d = paths.run_dir(st["repo"], st["slug"])
    name = f"{job}.{RUNNING[j['status']]}" if j["status"] in RUNNING else None
    if name and (d / f"{name}.proc").exists():
        proc.stop(d, name, proc.STOPPED_BY_MARCO)
        return
    questions.drop_kind(st, "worker", job=job)
    j.pop("question", None)
    _block(st, job, proc.STOPPED_BY_MARCO)


def _apply_stop_why(st: dict, item: dict) -> None:
    """`ark stop --why "..."`: the reason Marco gave. It lands on the block that follows his stop, or on the one
    that already happened, so the history says why instead of only "stopped by Marco"."""
    job, why = item.get("job"), item.get("why")
    j = st["jobs"].get(job)
    why = " ".join(_clean(why).split())[:300] if isinstance(why, str) else ""
    if j is None or not why:
        return
    if j["status"] == "blocked" and j.get("reason") == proc.STOPPED_BY_MARCO:
        j["reason"] = f"{proc.STOPPED_BY_MARCO}: {why}"
        return run.event(st, f"{job}: stop reason noted: {why}")
    if j["status"] not in run.TERMINAL:
        j["stop_why"] = why


ENDED = "run ended by Marco"


def _begin_end(st: dict, why: str) -> None:
    st["ending"] = why
    run.event(st, f"ending the run: {why}")


def _finish_ending(st: dict) -> None:
    """Stop what runs, keep what it cost, block what is open. What merged stays on ark/<slug>, and no final PR is
    opened: that is a decision about half a plan, and it is his. If a process cannot be stopped yet, the next cycle
    tries again, and until then the run takes no other step."""
    d = paths.run_dir(st["repo"], st["slug"])
    for name in proc.active(d):
        if name not in proc.finished(d):
            proc.stop(d, name, st["ending"])
    for name in proc.finished(d):
        try:
            r = proc.consume(d, name)
        except OSError:
            proc.discard(d, name)
            continue
        if r:
            _charge(st, name, r)
    if proc.active(d):
        return
    why = st.pop("ending")
    for job, j in st["jobs"].items():
        if j["status"] not in run.TERMINAL:
            j.pop("question", None)
            _block(st, job, why)
    st.update(status="blocked", reason=why, questions=[])
    _closed(st)
    run.alert(st, f"run blocked: {why}. No final PR was opened; what merged stays on ark/{st['slug']}, and the "
                  "worktrees are still there", critical=True)


def _answered_plan_changed(st: dict, q: dict, text: str) -> None:
    _begin_end(st, ENDED)


def _answered_cap_run(st: dict, q: dict, text: str) -> None:
    decision = st.pop("cap_decision", None)
    if decision:
        try:
            euklides.set_marco(decision, text)
        except OSError as err:
            run.event(st, f"the Euklides journal could not be written: {err}")
    words = text.split()
    if words == ["stop"]:
        return _begin_end(st, ENDED)
    st["usd_raised"] = st.get("usd_raised", 0) + float(words[1])
    st.pop("cap_hold", None)
    run.event(st, f"budget raised by ${float(words[1]):.2f}")


RUN_EUKLIDES = f"{euklides.RUN}.euklides"  # the run-level Euklides call, when a run is over its total
RUN_TOOLS = ["Read", "Grep", "Glob"]  # no worktree in this call: it reads, never edits or runs


def _run_budget(c: Ctx) -> tuple[float, float, float]:
    """(spent, planned total, the total with Marco's and Euklides' raises)."""
    st, planned = c.st, caps.run_budget(c.mjobs)
    return caps.run_spent(st), planned, planned + st.get("usd_raised", 0) + st.get("euklides_raised", 0)


def _ask_cap_run(st: dict, spent: float, budget: float, euk: str = "", planned: float = 0,
                 usd: float | None = None) -> None:
    """Marco decides: raise by Euklides' amount (or a quarter of the planned total), or end the run."""
    n = usd if usd is not None and math.isfinite(usd) and usd > 0 else max(1, math.ceil(planned * 0.25))
    amount = f"{n:g}"
    options = [{"label": f"Raise ${amount}", "detail": f"add ${amount} to the run's budget and go on",
                "answer": f"raise {amount}", "confirm": True},
               {"label": "End the run", "detail": "running workers finish; the run ends", "answer": "stop",
                "confirm": True}]
    questions.ask(st, "cap_run", f"This run spent ${spent:.2f}, over the ${budget:.2f} of its budgets. Running "
                                 "workers finish; nothing new starts. Answer `raise <usd>` to add budget, or "
                                 f"`stop` to end the run.{euk}", critical=True, options=options)


def _check_run_cap(c: Ctx) -> None:
    """Over its budgets, a run holds: what runs finishes, nothing new starts. Euklides decides first while his
    margin (a share of the planned total, a total across his raises) is left; then Marco decides."""
    st = c.st
    spent, planned, budget = _run_budget(c)
    if spent <= budget or all(j["status"] in run.TERMINAL for j in st["jobs"].values()):
        st.pop("cap_hold", None)
        questions.drop_kind(st, "cap_run")
    elif not st.get("cap_hold"):
        st["cap_hold"] = True
        cfg = euklides.settings()
        left = euklides.run_margin_left(st, planned, cfg["budget_margin"])
        if left > 1e-9 and st.get("euklides_run_count", 0) < cfg["decisions_per_job"]:
            return _start_run_euklides(c, spent, planned, left, cfg)
        _ask_cap_run(st, spent, budget, planned=planned)


def _start_run_euklides(c: Ctx, spent: float, planned: float, left: float, cfg: dict) -> None:
    st = c.st
    situation = f"the run spent ${spent:.2f}, over its planned total of ${planned:.2f} and its raises"
    prompt = brief.euklides_run_prompt(st, spent=spent, planned=planned, usd_raised=st.get("usd_raised", 0),
                                       euklides_raised=st.get("euklides_raised", 0), margin_left=left,
                                       allowed=euklides.RUN_ACTIONS, lessons=euklides.lessons(),
                                       earlier=[d for d in euklides.decisions() if str(d.get("id", "")).startswith(
                                           f"{st['repo']}/{st['slug']}/{euklides.RUN}/")][-10:])
    cmd = claude.agent_cmd("euklides", cfg["model"], prompt, session=str(uuid.uuid4()), usd=cfg["usd_per_call"],
                           tools=[t for t in RUN_TOOLS if t not in cfg["denied_tools"]],
                           denied_tools=sorted(set(cfg["denied_tools"]) | {"Bash", "Edit", "Write"}))
    env = claude.runner_env("euklides", repo_name=st["repo"])
    proc.start(c.dir, RUN_EUKLIDES, cmd, cwd=ticks_dir(), env=env, minutes=cfg["minutes_per_call"])
    st["cap_euklides"] = {"situation": situation}
    run.event(st, f"{situation}: Euklides decides first")


def on_run_euklides(c: Ctx, r: dict) -> None:
    """Euklides' answer on a run over its total: a raise within his margin lets the run go on; past it, an
    `ask` or no usable answer opens Marco's budget question with his note and proposal."""
    st = c.st
    e = st.pop("cap_euklides", None)
    if e is None:
        return run.event(st, f"ignored the result of {RUN_EUKLIDES}: nothing was waiting for it")
    d = euklides.parse(_answer(r) if _clean_exit(r) else None, euklides.RUN_ACTIONS, then=False)
    valid = d is not None
    d = d or NO_ANSWER
    try:
        decision = euklides.record(st, euklides.RUN, {"trigger": "cap_run", **e}, d, valid=valid, usd=_usd(r.get("usd")))
    except OSError as err:
        decision = None
        run.event(st, f"the Euklides journal could not be written: {err}")
    run.event(st, f"run euklides: {d['action']} — {_clean(d['note'])[:300]}")
    spent, planned, budget = _run_budget(c)
    left = euklides.run_margin_left(st, planned, euklides.settings()["budget_margin"])
    if d["action"] == "raise" and d["usd"] <= left + 1e-9:
        st["euklides_raised"] = round(st.get("euklides_raised", 0) + d["usd"], 4)
        st["euklides_run_count"] = st.get("euklides_run_count", 0) + 1
        st.pop("cap_hold", None)
        return run.event(st, f"Euklides raised the run's budget by ${d['usd']:.2f} "
                             f"(${st['euklides_raised']:.2f} raised by him in all)")
    st["cap_hold"] = True
    if spent <= budget:
        return
    proposal = f"raise {d['usd']:g}" if d["action"] == "raise" else (d["proposal"] or "stop")
    past = f" (a raise of ${d['usd']:.2f}, past the ${max(0, left):.2f} left of his margin)" \
        if d["action"] == "raise" else ""
    st["cap_decision"] = decision
    _ask_cap_run(st, spent, budget, f"\n\nEuklides{past}: {_clean(d['note'])}\n\nProposal: {_clean(proposal)}",
                 planned=planned, usd=_raise_usd(proposal))


def _raise_usd(proposal: str) -> float | None:
    """The amount of a run-level proposal `raise <number>`, or None."""
    words = str(proposal).split()
    if len(words) == 2 and words[0] == "raise":
        try:
            return float(words[1])
        except ValueError:
            return None
    return None


PLAN_CHANGED_OPTIONS = [{"label": "End the run", "detail": "the run ends; restore or approve the plan to go on instead",
                         "answer": "stop", "confirm": True}]


def _retry_stops(st: dict) -> None:
    """A stop that could not finish in its cycle is asked again: the decision stands in the state."""
    d = paths.run_dir(st["repo"], st["slug"])
    for job, j in st["jobs"].items():
        if j["status"] != "working":
            continue
        for key, reason in (("stop_then", INTERVENED), ("steering", STEERED), ("attaching", ATTACHED),
                            ("api_hung", API_HUNG)):
            if key in j and (d / f"{job}.worker.proc").exists() and not (d / f"{job}.worker.receipt.json").exists():
                proc.stop(d, f"{job}.worker", reason)
                break


def _apply_steer(st: dict, item: dict) -> None:
    job, text = item.get("job"), item.get("text")
    j = st["jobs"].get(job)
    if j is None or j["status"] != "working" or any(k in j for k in ("stop_then", "attaching")) or not isinstance(text, str):
        return run.alert(st, f"the steer for {_clean(str(job))} was not delivered: its worker is not running")
    _steer(st, job, text)


def _apply_attach(st: dict, item: dict) -> None:
    job = item.get("job")
    j = st["jobs"].get(job)
    if j is not None and j["status"] == "working":
        j["attaching"] = True
        proc.stop(paths.run_dir(st["repo"], st["slug"]), f"{job}.worker", ATTACHED)
    elif j is not None and j["status"] == "waiting_answer":
        questions.drop_kind(st, "worker", job=job)
        j.pop("question", None)
        j["status"] = "attached"
        run.event(st, f"{job}: handed to Marco")
    else:
        run.event(st, f"an attach for {_clean(str(job))} found nothing to hand over")


def _apply_attach_done(st: dict, item: dict) -> None:
    j = st["jobs"].get(item.get("job"))
    if j is not None and j["status"] == "attached":
        j["attach_done"] = True


def _finish_attach(c: Ctx) -> None:
    """He left a clean worktree: what he did takes the same road as a worker that ended DONE."""
    for job, j in c.st["jobs"].items():
        if j["status"] == "attached" and j.pop("attach_done", None):
            done = {"reason": "", "exit": 0, "result": "STATUS: DONE", "session": j["session"], "is_error": False, "meta": {}}
            try:
                on_worker(c, job, done)
            except Exception as e:
                fail(c, job, f"runner error after attach: {type(e).__name__}: {e}")


RETRY_STEP = {"waiting_fix": "needs_integrate", "working": "needs_worker", "euklides": "needs_worker", "waiting_answer": "needs_worker",
              "attached": "needs_worker", "needs_euklides": "needs_worker", "gating": "needs_gate",
              "reviewing": "needs_review", "integrating": "needs_integrate", "needs_merge": "needs_integrate",
              "waiting_dep": "pending"}
RETRY_CLEARS = ("reason", "not_before", "step_tries", "infra_tries", "holder_tries", "push_tries", "integrate_lost",
                "integrate_skips", "errors", "error", "euklides_count", "blocked_from", "waiting_on", "euklides",
                "euklides_ask", "question", "accept_gate")


def _retry_status(j: dict) -> str:
    was = j.get("blocked_from")
    if was in RETRY_STEP:
        return RETRY_STEP[was]
    if was and (was == "pending" or was.startswith("needs_")):
        return was
    return "needs_push" if j.get("head") else "needs_worker"  # blocked by code older than blocked_from


def _abandoned(st: dict) -> str | None:
    """The day an abandoned run became so, or None: such a run is never retried, only planned again."""
    since = sweep.abandoned(st, datetime.now(timezone.utc))
    return since.date().isoformat() if since else None


def _drop_retry(st: dict, item: dict, since: str) -> None:
    run.event(st, f"a retry was dropped: the run was abandoned on {since}")
    inbox.done(item)


def _apply_retry(st: dict, item: dict) -> None:
    """`ark retry`: the job goes back to the step it was blocked from, with that step's counters cleared, and every
    job blocked only because it waited on it goes back to pending."""
    since = _abandoned(st)
    if since:
        return _drop_retry(st, item, since)
    job = item.get("job")
    j = st["jobs"].get(job)
    if j is None or j["status"] != "blocked":
        return run.event(st, f"a retry for {_clean(str(job))} found nothing blocked")
    status = _retry_status(j)
    for k in RETRY_CLEARS:
        j.pop(k, None)
    j.update(status=status, integrate_fails=0, review_errors=0)
    questions.drop_kind(st, "worker", job=job)
    run.event(st, f"{job}: retried by Marco, back to {status}")
    moved = True
    while moved:
        moved = False
        for name, d in st["jobs"].items():
            dep = d.get("waiting_on")
            if d["status"] == "blocked" and dep and st["jobs"][dep]["status"] != "blocked":
                d.update(status="pending")
                for k in ("reason", "blocked_from", "waiting_on"):
                    d.pop(k, None)
                run.event(st, f"{name}: back to pending, {dep} is no longer blocked")
                moved = True


def _reopen(st: dict, states: list[dict] | None = None) -> None:
    """A closed blocked run with a retry for one of its blocked jobs runs again; its final PR stays and its
    decisions doc is written again when it closes. Not into the area of a run that is live now: the retry is
    refused, and he hears who holds it."""
    items = [i for i in inbox.pending(st["repo"], st["slug"]) if i["kind"] == "retry"]
    since = _abandoned(st) if items else None
    if since:
        for i in items:
            _drop_retry(st, i, since)
        return
    if not any((st["jobs"].get(i.get("job")) or {}).get("status") == "blocked" for i in items):
        return
    clash = approve.area_clash(st, states or []) if st.get("closing") == "done" else None
    if clash:
        for i in items:
            inbox.done(i)
        return run.alert(st, f"retry refused: {clash}. It stays blocked; retry again once that run ends")
    st.update(status="running", closing=None)
    for k in ("decisions_sha", "closing_wait", "closing_errors", "decisions_ticks", "closed_at"):
        st.pop(k, None)
    run.event(st, "reopened by ark retry")


def _rerecord(st: dict, forge) -> None:
    """The history row of a run that closed already says how it ended then: write it again with how it ends now."""
    try:
        c = Ctx(st, forge)
    except Exception as e:  # without its plan or its repo there is no row to correct, and the new status stands
        return run.event(st, f"history not updated: {_clean(f'{type(e).__name__}: {e}')[:300]}")
    _close_report(c, record=True)


def _abort(st: dict, forge) -> None:
    """`ark abort`: Marco ends a blocked or waiting run for good. It is never stepped again and `ark retry` cannot
    reopen it; its history row says aborted."""
    items = [i for i in inbox.pending(st["repo"], st["slug"]) if i["kind"] == "abort"]
    if not items:
        return
    was = st["status"]
    why = _clean(str(items[-1].get("why") or "")) or "ended by Marco"
    for k in ("decisions_sha", "closing_wait", "closing_errors", "decisions_ticks"):
        st.pop(k, None)
    st.update(status="aborted", reason=why, questions=[])
    _closed(st)
    run.event(st, f"aborted by Marco: {why}")
    if was == "blocked":
        _rerecord(st, forge)


FINAL_PR_EVERY = 600  # seconds between two looks at the final PR of a blocked run
_final_pr_seen: dict[str, float] = {}


def _settle_final_pr(st: dict, forge) -> None:
    """A run closes `blocked` while any job is unmerged, even when the jobs that mattered merged and Marco merged the
    final PR. Once that PR is merged the run is done: what was left blocked stays on record in its jobs."""
    pr = st.get("final_pr")
    if st.get("closing") != "done" or not pr:
        return
    key = str(paths.run_dir(st["repo"], st["slug"]))
    if time.monotonic() - _final_pr_seen.get(key, -FINAL_PR_EVERY) < FINAL_PR_EVERY:
        return
    _final_pr_seen[key] = time.monotonic()
    repo = paths.find_repo(st["repo"])
    if repo is None:
        return
    try:
        state = forge.view(repo, pr["number"])["state"]
    except forge_mod.ForgeError:
        return  # the next look is in ten minutes
    if state != "MERGED":
        return
    st.update(status="done", reason="")
    run.event(st, f"the final PR {pr['url']} is merged: the run is done")
    _rerecord(st, forge)


ANSWERS = {"worker": _answered_worker, "plan_changed": _answered_plan_changed, "cap_run": _answered_cap_run,
           "refresh": _answered_refresh}
INBOX = {"answer": _apply_answer, "stop": _apply_stop, "stop_why": _apply_stop_why, "steer": _apply_steer,
         "attach": _apply_attach, "attach_done": _apply_attach_done, "retry": _apply_retry}


def apply_inbox(st: dict) -> None:
    """Every item is safe to apply twice: if the save fails they stay, and the next cycle starts from the old state."""
    items = inbox.pending(st["repo"], st["slug"])
    if not items:
        return
    for item in items:
        fn = INBOX.get(item["kind"])
        try:
            if fn is None:
                run.event(st, f"ignored an inbox item of kind {_clean(item['kind'])}")
            else:
                fn(st, item)
        except Exception as e:  # the item goes anyway: a broken one must not come back every cycle
            run.event(st, f"inbox item {_clean(item['kind'])} failed: {type(e).__name__}: {_clean(str(e))}")
    if _save_now(st):
        for item in items:
            inbox.done(item)


def step(st: dict, states: list[dict], forge, slots: Slots, hold: bool = False) -> None:
    if st["status"] == "blocked":
        _reopen(st, states)
    if st["status"] in ("blocked", "waiting"):
        _abort(st, forge)
    if st["status"] == "blocked":
        _settle_final_pr(st, forge)
    if st["status"] in run.FINISHED:
        inbox.drop_all(st["repo"], st["slug"])  # nobody will read them
        return
    if st["status"] == "waiting":
        if slots.capped(st["repo"]) or hold:  # a run that has not started spends nothing by waiting
            return
        if not try_start(st, states, forge):
            return
        if not _save_now(st):  # the jobs exist now: a crash below finds its worker by them, not by chance
            return
    proc.reap(paths.run_dir(st["repo"], st["slug"]))  # first: nothing below can excuse a process from its deadline
    apply_inbox(st)
    _remind_questions(st)
    _retry_stops(st)
    if st.get("ending"):
        return _finish_ending(st)
    if merge.plan_reason(st):  # the same question ark-merge asks: a run it would refuse takes no step
        if st["status"] != "held":
            st["status"] = "held"
            run.alert(st, "the plan changed after its approval: nothing new is dispatched. Approve the new text "
                          "(`ark approve`) and the run takes it as the next version.")
        questions.ask(st, "plan_changed", "The plan changed after its approval, so this run is held. To go on, restore "
                                          "the approved text or approve the new one. Answer `stop` to end the run.",
                      quiet=True, options=PLAN_CHANGED_OPTIONS)  # the alert above says it; ask again finds the same one
        return
    if st["status"] == "held":
        st["status"] = "running"
        st.pop("reapprove_refused", None)
        questions.drop_kind(st, "plan_changed")
        run.event(st, "the approved plan is back: running again")
    c = Ctx(st, forge)
    _finish_attach(c)
    if consume_receipts(c):
        _check_run_cap(c)
        if slots.capped(st["repo"]) or hold:  # closing pays for a tick too; after a wake, nothing starts before the network
            return
        if not st.get("cap_hold"):
            schedule(c, slots)
            watch(c)
        maybe_close(c)


def _first_refusal(st: dict, a: dict, reason: str) -> bool:
    """True the first time this approval is refused for this reason: he hears each reason once, and a new one
    after another (an area that overlaps, then one that does not validate) is heard too."""
    key = f"{a['sha256']}:{reason}"
    if st.get("reapprove_refused") == key:
        return False
    st["reapprove_refused"] = key
    return True


def _old_plan(st: dict):
    """The frozen text this run follows now, or None when it was never kept."""
    v = versions.by_sha(st["repo"], st["slug"], st["plan_sha"])
    return versions.plan_path(st["repo"], st["slug"], v["version"]) if v and v.get("kept") else None


def _drop_job_work(st: dict, job: str, j: dict, forge, repo, *, close_pr: bool) -> None:
    """Stop a job's processes, close its PR, delete its branch and worktree: its work is stale."""
    d = paths.run_dir(st["repo"], st["slug"])
    for info in proc.job_processes(d, job):
        name = info.get("name") or ""
        if name:
            proc.stop(d, name, run.REPLANNED)
            proc.discard(d, name)
    pr = j.get("pr")
    if close_pr and isinstance(pr, dict) and type(pr.get("number")) is int:
        try:
            if forge.view(repo, pr["number"])["state"] == "OPEN":
                forge.close(repo, pr["number"], f"Closed by Arkimedes: a new version of the plan {st['slug']} "
                                                f"changed or removed the job {job}.")
        except forge_mod.ForgeError as e:
            run.alert(st, f"could not close the PR of {job} ({pr.get('url')}): {_clean(str(e))}")
    gitops.delete_remote(repo, f"ark/{st['slug']}-{job}")
    gitops.remove_worktree(repo, _wt(st, job))
    merge.drop_records(d, job)


def _first_version(st: dict) -> None:
    """The version a run starts under, when its approval was versioned: st["versions"] begins there."""
    if not st.get("versions"):
        v = versions.by_sha(st["repo"], st["slug"], st["plan_sha"])
        if v:
            st["versions"] = [{"version": v["version"], "approved_at": st["approved_at"], "changes": {}}]


def _adopt_version(st: dict, a: dict, states: list[dict], forge) -> None:
    """A newer approved version of this plan: apply only what changed, in any run state but aborted. An
    unchanged job goes on as it is; a changed one starts over from the new text (a merged one as a follow-up);
    a removed one loses its processes, PR and branch (a merged one stays in main, and Marco hears it); an
    added one is scheduled. A done or blocked run starts again under the same slug. A refusal is said once."""
    if a["sha256"] == st["plan_sha"] or type(a.get("version")) is not int or st["status"] == "aborted":
        return
    new = Path(a["plan"])
    if not new.is_file() or sha256_file(new) != a["sha256"]:
        return
    why, area, mjobs = "", [], {}
    problems = validate(new, approved=True)
    if problems:
        why = f"it does not validate: {problems[0].message}"
    else:
        mjobs = {mj["id"]: mj for mj in load_plan(new).manifest["jobs"]}
        area = [g for mj in mjobs.values() for g in mj["area"]]
        for o in states:
            if o is not st and o["repo"] == st["repo"] and o["status"] in ("running", "held") \
                    and areas_overlap(area, o.get("area") or []):
                why = f"its area overlaps the {o['status']} plan {o['slug']}"
                break
    if why:
        if _first_refusal(st, a, why):
            run.alert(st, f"it cannot take v{a['version']}: {why}. It goes on with the version it follows.")
        return
    repo = paths.find_repo(st["repo"])
    if repo is None:
        raise gitops.GitError(f"repo {st['repo']} not found under a workspace root")
    old = _old_plan(st)
    prev = versions.by_sha(st["repo"], st["slug"], st["plan_sha"])
    old_n = prev["version"] if prev else a["version"] - 1
    changes = versions.job_changes(old, new) if st["jobs"] else {}
    for jid in st["jobs"]:  # a legacy run has no kept text: a job the new manifest lacks is removed
        if jid not in mjobs:
            changes.setdefault(jid, "removed")
    retired = st.setdefault("retired_jobs", {})
    told = []
    for jid, kind in changes.items():
        j = st["jobs"].get(jid)
        if kind == "added" or j is None:
            if jid in mjobs:
                st["jobs"][jid] = run.new_job(mjobs[jid])
            continue
        if kind == "unchanged":
            j["heavy"] = bool(mjobs[jid].get("heavy"))
            continue
        merged = j["status"] == "merged"
        _drop_job_work(st, jid, j, forge, repo, close_pr=not merged)
        if kind == "removed":
            retired[f"{jid}@v{old_n}" if merged else jid] = {**j, "removed_in": a["version"]}
            del st["jobs"][jid]
            if merged:
                told.append(f"{jid} was removed in v{a['version']} but merged under v{old_n}: it stays in main")
            continue
        fresh = run.new_job(mjobs[jid])  # changed
        if merged:
            retired[f"{jid}@v{old_n}"] = j
            fresh["followup"] = {"from_version": old_n, "diff": versions.job_diff(old, new, jid)}
        st["jobs"][jid] = fresh
    if not st.get("versions"):
        st["versions"] = [{"version": old_n, "approved_at": st["approved_at"], "changes": {}}]
    st["versions"].append({"version": a["version"], "approved_at": a["approved_at"], "changes": changes})
    st.pop("reapprove_refused", None)
    st.update(plan=a["plan"], plan_sha=a["sha256"], approved_at=a["approved_at"], area=area)
    if st["status"] in ("done", "blocked"):
        _restart_finished(st, forge, repo)
    for line in told:
        run.alert(st, line)
    summary = ", ".join(f"{k} {', '.join(sorted(j for j, c in changes.items() if c == k))}"
                        for k in ("changed", "added", "removed") if k in changes.values()) or "no job changed"
    run.event(st, f"v{a['version']} adopted: {summary}")


def _restart_finished(st: dict, forge, repo) -> None:
    """A done or blocked run takes a new version. An open final PR keeps ark/<slug> and grows with the
    new work; otherwise main already holds what merged, so the run starts again from main."""
    pr = st.get("final_pr")
    is_open = False
    if pr:
        try:
            is_open = forge.view(repo, pr["number"])["state"] == "OPEN"
        except forge_mod.ForgeError:
            is_open = True  # unsure: keep the branch, never lose work
    for k in ("decisions_sha", "decisions_ticks", "closing_wait", "closing_errors", "closing_error", "closed_at"):
        st.pop(k, None)
    st.update(closing=None, reason="")
    if is_open:
        st["status"] = "running"
        return
    gitops.delete_remote(repo, f"ark/{st['slug']}")
    st.update(status="waiting", base_sha=None, final_pr=None)


def status_line(states: list[dict]) -> str:
    live = [s for s in states if s["status"] not in run.FINISHED]
    finished = len(states) - len(live)
    parts = []
    for s in live:
        n, m = len(s["jobs"]), sum(j["status"] == "merged" for j in s["jobs"].values())
        parts.append(f"{s['repo']}/{s['slug']} {s['status']}" + (f" {m}/{n} merged" if n else ""))
    line = " · ".join(parts) or "idle"
    asked = sum(len(s.get("questions") or []) for s in live)
    return line + (f" · {finished} finished" if finished else "") + (f" · ❓{asked}" if asked else "")


def _good_questions(st: dict) -> bool:
    """The cycle reads these outside any run's guard (questions.md, the status line): damage here would stop every run."""
    if "questions" not in st:
        return True
    return isinstance(st["questions"], list) and all(
        isinstance(q, dict) and all(isinstance(q.get(k), str) for k in ("id", "kind", "text"))
        and "job" in q and (q["job"] is None or isinstance(q["job"], str)) for q in st["questions"])


def _migrate(st: dict) -> dict:
    """A run saved before Euklides: its judge step is a Euklides step. A judge still running is left
    to end; its receipt finds nothing waiting for it."""
    for j in st["jobs"].values():
        if j["status"] in ("needs_judge", "judging"):
            trigger = "question" if j.get("question") else "failure"
            j["status"] = "needs_euklides"
            j.setdefault("euklides", {"trigger": trigger, "situation": j.get("question") or j.get("failure") or "",
                                      "kind": None})
    if "base_green" not in st and st.get("status") not in ("waiting", None):  # a run started by an older loop
        if any(j["status"] != "pending" for j in st["jobs"].values()):
            st["base_green"] = True
        else:
            st["base_green"] = False
            st.setdefault("refreshes", [])
            if not st.get("refresh"):
                st["refresh"] = _new_refresh(0, st.get("base_sha"), None)
                st["main_seen"] = st.get("base_sha")
    return st


def _load(repo: str, slug: str) -> dict | None:
    """run.load, plus the shape the loop relies on: valid JSON of the wrong shape is damage too."""
    st = run.load(repo, slug)
    if st is None:
        return None
    jobs = st.get("jobs")
    if (merge.state_reason(st) or (st["repo"], st["slug"]) != (repo, slug) or not isinstance(st.get("status"), str)
            or not isinstance(st.get("events"), list) or not isinstance(st.get("alerts"), list) or not _good_questions(st)
            or not all(isinstance(j, dict) and isinstance(j.get("status"), str) for j in jobs.values())):
        raise run.StateCorrupt(f"malformed run state {run.state_file(repo, slug)}")
    return _migrate({**_clean(st), "repo": repo, "slug": slug})  # a lone surrogate is valid JSON, and no file can hold it; these two name the folder


def _once(key: tuple, text: str, alerts: list[str]) -> bool:
    """Whether this is the first time."""
    key = (str(paths.ark_home()), *key)
    if key in _said:
        return False
    _said.add(key)
    alerts.append(text)
    return True


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
            if st and st["status"] in run.FINISHED:
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


def _remind_questions(st: dict) -> None:
    """A run waiting for Marco never closes by itself: he hears about each open question once a day."""
    now = time.time()
    for q in st.get("questions") or []:
        at = q.setdefault("remind_at", now + REMIND_EVERY)
        if at <= now:
            q["remind_at"] = now + REMIND_EVERY
            who = f" from {q['job']}" if q.get("job") else ""
            run.alert(st, f"still waiting for your answer to {q['id']}{who}: {q['text'][:200]} — `ark answer`",
                      critical=True)


def _nothing_to_advance(states: list[dict], capped_accounts=frozenset()) -> bool:
    """Called after every run took its step: there are live runs, no process alive anywhere, and each run is
    held or waits for Marco. A pending job beside a waiting one only waits for it: this pass started all it could.
    A run on a capped account waits for Marco like a held one."""
    capped_accounts = capped_accounts or frozenset()
    live = [st for st in states if st["status"] not in (*run.FINISHED, "waiting")]
    if not live or any(proc.active(paths.run_dir(st["repo"], st["slug"])) for st in live):
        return False
    for st in live:
        if st["status"] == "held" or st.get("cap_hold") or accounts.account_of(st["repo"]) in capped_accounts:
            continue
        statuses = [j["status"] for j in st["jobs"].values() if j["status"] not in run.TERMINAL]
        if not any(s in WAITS_FOR_MARCO for s in statuses):
            return False
        if any(s not in WAITS_FOR_MARCO and s not in ("pending", "waiting_dep") for s in statuses):
            return False
    return True


def _flag_stuck(states: list[dict], capped_accounts, alerts: list[str], critical: list[str]) -> None:
    key = ("stuck",)
    if not _nothing_to_advance(states, capped_accounts):
        _said.discard((str(paths.ark_home()), *key))
        return
    text = "nothing left to advance: every live run waits for you. See `ark ps` and `ark answer`."
    if _once(key, text, alerts):
        critical.append(text)


def cycle(forge=None) -> tuple[str, list[str], bool]:
    """One pass. Returns (status line, alerts to show, whether any run state changed)."""
    forge = forge or forge_mod.get()
    limits = paths.load_workspace()["limits"]  # first: if it raises, no alert was consumed and lost
    euklides.settings()  # a bad euklides value raises here too, before any process starts
    sweep.settings()  # and a bad sweep value
    caps.check_limits(limits)  # a bad cap, personal or on a rule, raises here too: before any alert is consumed
    states, before, alerts, approved = {}, {}, [], {}
    changed, critical = False, []
    # Account questions, in this order: apply an answer, mark its inbox item done, save runs/daycap.json at the end of
    # the cycle's account block. If that save fails, a raise is already in raises.jsonl, so the account is no longer
    # capped and drop_except closes the stale question next cycle; a `stop` is lost and the question stays open,
    # which is the safe side (the account keeps waiting).
    day = daycap.load()
    for item in daycap.pending():
        said = daycap.apply(day, item.get("id"), str(item.get("text", ""))) if item["kind"] == "answer" else None
        inbox.done(item)  # right away: an answer applied twice would record its raise twice
        if said:
            alerts.append(said)
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
        if states[key]["status"] == "waiting" and not states[key]["jobs"]:  # nothing started: the newer wins
            states[key].update(plan=a["plan"], plan_sha=a["sha256"], approved_at=a["approved_at"])
    _orphans(approved, alerts)
    for key, st in states.items():
        _guarded(st, "reapprove_error", lambda st=st, a=approved[key]: _adopt_version(st, a, list(states.values()), forge))
    for st in states.values():
        _guarded(st, "reconcile_error", lambda st=st: reconcile(st))
    slots = Slots(states.values(), limits)
    net = net_hold()
    if net == "gave_up":
        for st in states.values():
            if st["status"] not in run.FINISHED:
                run.event(st, "the network did not come back in 10 minutes; going on")
    home = str(paths.ark_home())
    for key, st in states.items():
        if (home, "unsaved", *key) in _said:  # moving it on would pay again for work it cannot remember
            _guarded(st, "error", lambda d=paths.run_dir(*key): proc.reap(d))
        else:
            _guarded(st, "error", lambda st=st: step(st, list(states.values()), forge, slots, hold=net == "wait"))
    origins = {}
    waiting = {}
    for st in states.values():
        if st["status"] not in run.FINISHED:
            waiting.setdefault(accounts.account_of(st["repo"]), []).append(f"{st['repo']}/{st['slug']}")
    for account in sorted(slots.capped_accounts):
        limit = caps.day_limit(limits, account) + caps.raised_today(account)
        runs_ = ", ".join(waiting.get(account, [])) or "no live run"
        q, new = daycap.ask(day, account, (
            f"{account} spent ${caps.spent_today(account):.2f} today; its cap is ${limit:.2f}. Running work finishes; "
            f"nothing new starts on {runs_}. Answer `raise <usd>` to add to today's cap, or `stop` to wait until tomorrow."))
        if new:
            text = f"{account}: daily spending cap reached · question {q['id']}: {q['text']} — `ark answer`"
            alerts.append(text)
            critical.append(text)
        elif q is None:
            _once(("day_cap_stopped", caps.today(), account), f"{account}: at its daily cap; waits until tomorrow", alerts)
    daycap.drop_except(day, slots.capped_accounts)
    try:
        daycap.save(day)
    except OSError as e:
        _once(("daycap_file",), f"cannot write runs/daycap.json: {e}", alerts)
    for key, st in states.items():
        run.stamp_times(st)
        for line in st["alerts"]:
            origins[line] = notify.origin(st, line)
        alerts += st["alerts"]
        critical += st.pop("critical", [])
        st["alerts"] = []
        dirty = json.dumps(st, sort_keys=True) != before[key]
        changed = changed or dirty
        if dirty or (home, "unsaved", *key) in _said:
            try:
                run.save(st)
                _said.discard((home, "unsaved", *key))
            except Exception as e:  # until a save works this run only reaps; then reconcile() catches up
                text = f"{key[0]}/{key[1]}: the run state cannot be saved: {type(e).__name__}: {e}"
                if _once(("unsaved", *key), text, alerts):
                    critical.append(text)
    _flag_stuck(list(states.values()), slots.capped_accounts, alerts, critical)
    try:
        questions.save_file(list(states.values()), day["questions"])
    except OSError as e:
        _once(("questions_file",), f"cannot write questions.md: {e}", alerts)
    try:
        sweep.daily(forge, alerts)  # once a local day: what finished and abandoned runs left behind
    except Exception as e:  # the cycle's status and alerts still go out
        alerts.append(f"sweep: {type(e).__name__}: {e}")
    alerts, critical, origins = _clean(alerts), _clean(critical), _clean(origins)  # an approval's names reach them unchecked; notify catches only OSError
    problem = notify.deliver(alerts, critical, origins)  # never raises: it says what went wrong
    if problem:
        _once(("notify",), problem, alerts)
    line = status_line(list(states.values()))
    return ("esperando red tras suspensi\u00f3n \u00b7 " + line if net == "wait" else line), alerts, changed


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
    for f in sorted((paths.ark_home() / "inbox").glob("**/*.json")):
        h.update(f"{f}\n".encode("utf-8", "replace"))
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
    if _net_wait_until:
        return True
    now = time.time()
    for a in approvals():
        try:
            st = _load(a["repo"], a["slug"])
        except Exception:  # the cycle says so, once
            continue
        if st and st.get("ending"):
            return True
        if not st or st["status"] != "running":
            continue
        if st.get("closing_errors") and st.get("closing_wait", 0) <= now:
            return True
        for j in st["jobs"].values():
            if j["status"] in run.TERMINAL:
                continue
            if (j["status"] == "working" and not st.get("cap_hold") and 0 < j.get("soft_deadline", 0) <= now
                    and not any(k in j for k in HANDLING)):
                return True
            if j.get("errors"):  # a running job has them only while its result cannot be archived
                return True
            if j["status"] not in RUNNING and 0 < j.get("not_before", 0) <= now:
                return True
        for f in st.get("fixes") or []:
            if f["status"] in FIX_WAITING and 0 < f.get("not_before", 0) <= now:
                return True
    return False


def _setting(ws: dict, key: str) -> float:
    """A positive number: a sleep of a bad value ends the loop, and of 0 it spins."""
    v = ws[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 86400:
        raise ValueError(f"workspace.json: {key} must be a positive number, not {v!r}")
    return v


def _beat(poll: float, said: str | None) -> str | None:
    """Write the heartbeat; print a failure once per distinct message. Returns what was said."""
    problem = beat.write(poll)
    if problem and problem != said:
        print(f"⚠ {problem}", flush=True)
    return problem


def code_version(code=None) -> str:
    """What a new process would load: the runner's Python files and its launcher."""
    code = Path(code or paths.code_dir())
    h = hashlib.sha256()
    for f in sorted([*(code / "lib" / "ark").glob("*.py"), code / "bin" / "ark-loop"]):
        if f.is_file():
            h.update(f.relative_to(code).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def _self_check(code=None) -> str | None:
    """None when the code on disk imports, reads the workspace and every run; otherwise why not."""
    code = Path(code or paths.code_dir())
    env = {**os.environ, "PYTHONPATH": str(code / "lib")}
    try:
        r = subprocess.run([sys.executable, "-m", "ark.loop", "--self-check"], cwd=code, env=env,
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"{type(e).__name__}: {e}"
    if r.returncode == 0:
        return None
    return _clean((r.stderr or r.stdout).strip()[-600:] or f"exit {r.returncode}")


def _maybe_restart(started: dict, argv) -> None:
    """New code on disk takes over between cycles; code that fails its self-check never does."""
    now = code_version()
    if now == started["version"] or now == started.get("bad"):
        return
    why = _self_check()
    if why:
        started["bad"] = now
        text = f"ark-loop: the new code ({now}) failed its self-check and was not loaded; still running {started['version']}: {why}"
        print(f"⚠ {text}", flush=True)
        notify.deliver([text], [text])
        return
    text = f"ark-loop: restarting on new code {started['version']} → {now}"
    print(text, flush=True)
    notify.deliver([text], [])
    os.execv(sys.executable, [sys.executable, "-m", "ark.loop", *argv])


def _polling(once: bool, argv=()) -> int:
    started = {"version": code_version(), "bad": None}
    poll_default = paths.DEFAULTS["poll_seconds"]
    last_line, last_sig, last_full, last_error, last_clocks = None, None, 0.0, None, None
    beat_poll, beat_said = poll_default, None
    while True:
        beat_said = _beat(beat_poll, beat_said)
        now_clocks = clocks()
        slept = slept_between(last_clocks, now_clocks) if last_clocks else 0
        last_clocks = now_clocks
        if slept:
            try:
                for line in on_wake(slept, now_clocks[0]):
                    print(f"⚠ {line}", flush=True)
            except Exception as e:  # the free failure still covers the processes
                print(f"⚠ the deadlines could not be moved after the sleep: {type(e).__name__}: {e}", flush=True)
            last_sig = None  # a cycle right away
        poll = poll_default
        try:
            ws = paths.load_workspace()
            poll, tick = _setting(ws, "poll_seconds"), _setting(ws, "tick_minutes") * 60
            beat_poll = poll
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
                beat_said = _beat(beat_poll, beat_said)
                if line != last_line:
                    print(f"{datetime.now():%H:%M} {line}", flush=True)
                    last_line = line
            last_error = None
        except Exception as e:  # a bad edit of workspace.json, a passing failure: the runner stays up
            text = f"{type(e).__name__}: {e}"
            if text != last_error:
                print(f"⚠ the cycle failed, retrying on every poll: {text}", flush=True)
            last_error, last_sig = text, None
            if once:
                return 1
        if once:
            return 0
        _maybe_restart(started, argv)
        time.sleep(poll)


def main(argv=None) -> int:
    # A shell without job control starts `ark-loop &` with SIGINT ignored, and then Python installs no handler
    # for it. It must stop on Ctrl-C however it was launched; what it starts gets the default back at exec.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    argv = sys.argv[1:] if argv is None else argv
    if set(argv) - {"--once", "--self-check"}:  # a typo must not start something that spends money
        print("usage: ark-loop [--once]", file=sys.stderr)
        return 2
    if "--self-check" in argv:  # run by a live loop on new code: never takes the lock, never starts anything
        paths.load_workspace()
        for a in approvals():
            run.load(a["repo"], a["slug"])
        signature()
        print("ok")
        return 0
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("ark-loop does not run with ANTHROPIC_API_KEY: the workers would bill per token instead of using "
              "your subscription. Run `unset ANTHROPIC_API_KEY` and start it again.", file=sys.stderr)
        return 2
    if os.environ.get("ARK_ROLE"):
        print("ark-loop does not run inside a runner process.", file=sys.stderr)
        return 2
    for name, where in (("ARK_HOME", paths.ark_home()), ("ARK_WORKTREES", paths.worktrees_dir())):
        if not where.is_absolute():  # the wrapper changed directory: it would land inside the code
            print(f"ark-loop needs an absolute path in {name}, not {where}.", file=sys.stderr)
            return 2
    sys.stdout.reconfigure(errors="backslashreplace")  # an alert the terminal cannot show must not raise
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    lock_file = paths.runs_dir() / ".loop.lock"
    try:  # never through a link, never truncated; the kernel releases it on any death
        lock = os.open(lock_file, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as e:
        print(f"ark-loop cannot open its lock {lock_file}: {e}", file=sys.stderr)
        return 1
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        print("ark-loop is already running.", file=sys.stderr)
        return 1
    try:
        return _polling("--once" in argv, argv)
    except KeyboardInterrupt:  # Ctrl-C is how it stops; workers live in their own sessions and go on
        return 130
    finally:
        os.close(lock)


if __name__ == "__main__":
    sys.exit(main())
