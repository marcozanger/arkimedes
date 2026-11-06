"""ark-loop: the always-on runner (§4, §5). No model: a state machine over files.

Each cycle: every approval → its run (runs/<repo>/<slug>/state.json); start waiting runs; hold a
run whose plan changed; consume process receipts; move every job forward within the limits.
Claude runs only as workers, reviewers and two read-only ticks (judge, decisions)."""
from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path

from . import brief, claude, forge as forge_mod, gitops, integrate, merge, paths, proc, run
from .globs import areas_overlap, match_any
from .plan import load_plan, sha256_file
from .validate import validate

ERROR_ALERT_AFTER = 20    # cycles (about 5 minutes) a step keeps failing before Marco hears about it
HOLDERS_PATIENCE = 900    # seconds a job waits for leftovers in its worktree before it blocks

# What a job's status says is running, and where the job goes back to if that process is not there.
RUNNING = {"working": "worker", "gating": "gate", "reviewing": "review", "integrating": "integrate", "judging": "judge"}
WAITING = {"worker": "needs_worker", "gate": "needs_gate", "review": "needs_review",
           "integrate": "needs_integrate", "judge": "needs_judge"}

_said: set[tuple] = set()  # what was already alerted about a run with no state to remember it in


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
        """A worker writes this file and its bytes are not trusted; a symlink is not a report."""
        f = self.report(job)
        if f.is_symlink() or not f.exists():
            return None
        return f.read_text(encoding="utf-8", errors="replace")


# ---- runs -------------------------------------------------------------------------------------

def plan_ok(st: dict) -> bool:
    p = Path(st["plan"])
    return p.exists() and sha256_file(p) == st["plan_sha"]


def try_start(st: dict, others: list[dict]) -> bool:
    """waiting → running: the approved plan still validates, the repo is there, no running run in
    the same repo has an overlapping area, ark/<slug> does not exist yet. Then push ark/<slug>."""
    plan = Path(st["plan"])
    problems = validate(plan, approved=True)
    reason, repo, default, area, existing = "", None, None, [], None
    if problems:
        reason = f"the plan does not validate: {problems[0].message}"
    else:
        repo = paths.find_repo(st["repo"])
        area = [g for j in load_plan(plan).manifest["jobs"] for g in j["area"]]
        if repo is None:
            reason = f"repo {st['repo']} not found under a workspace root"
    if not reason:
        for o in others:
            if o is not st and o["repo"] == st["repo"] and o["status"] in ("running", "held") \
                    and areas_overlap(area, o.get("area") or []):
                reason = f"its area overlaps the running plan {o['slug']}"
                break
    if not reason:
        try:
            gitops.fetch(repo)
            default = gitops.default_branch(repo)
            existing = gitops.remote_sha(repo, f"ark/{st['slug']}")
            if existing and existing != st["base_sha"]:
                reason = f"ark/{st['slug']} already exists on origin: delete it, or approve under another slug"
        except gitops.GitError as e:
            reason = str(e)
    if reason:
        if reason != st["reason"]:
            st["reason"] = reason
            run.event(st, f"waiting: {reason}")
        return False
    if existing:  # this run pushed it and died before saving "running"
        base = existing
    else:
        base = gitops.rev(repo, f"origin/{default}")
        st.update(default_branch=default, base_sha=base)
        run.save(st)  # on disk before the branch exists: the next cycle knows that branch is ours
        gitops.push_ref(repo, base, f"ark/{st['slug']}")
    st.update(status="running", reason="", default_branch=default, base_sha=base, area=area)
    for mj in load_plan(plan).manifest["jobs"]:
        st["jobs"][mj["id"]] = run.new_job(mj)
    run.event(st, f"started: ark/{st['slug']} from {default} @ {base[:8]}")
    return True


# ---- job transitions ---------------------------------------------------------------------------

def fail(c: Ctx, job: str, why: str) -> None:
    j = c.st["jobs"][job]
    j.update(status="needs_judge", failure=why, tier_attempts=j["tier_attempts"] + 1)
    run.event(c.st, f"{job} failed ({j['tier']}, attempt {j['tier_attempts']} at this tier): {why}")


def block(c: Ctx, job: str, why: str) -> None:
    c.st["jobs"][job].update(status="blocked", reason=why)
    run.alert(c.st, f"{job} blocked: {why}")


def allowed_actions(j: dict) -> list[str]:
    """block always; retry while fewer than 2 failures at this tier; escalate below T2."""
    out = ["block"]
    if j["tier_attempts"] < 2:
        out.append("retry")
    if j["tier"] in claude.NEXT_TIER:
        out.append("escalate")
    return out


def _error(c: Ctx, job: str, e: Exception) -> None:
    """A git / gh / disk error while moving a job: it stays where it is and is retried next cycle, since
    a laptop offline must not block anything. Marco hears about it once, if it does not go away."""
    text = f"{type(e).__name__}: {e}"
    j = c.st["jobs"][job]
    j["errors"] = j.get("errors", 0) + 1
    if j.get("error") != text:
        j["error"] = text
        run.event(c.st, f"{job}: {text} (retrying)")
    if j["errors"] == ERROR_ALERT_AFTER:
        run.alert(c.st, f"{job} has been failing for {ERROR_ALERT_AFTER} cycles, still retrying: {text}")


# ---- receipts ---------------------------------------------------------------------------------

def _clean_exit(r: dict) -> bool:
    return not r["reason"] and r["exit"] == 0 and not r.get("is_error")


def _printable(name: str) -> str:
    """A file name from git may hold undecodable bytes, and the run state must stay writable."""
    return name.encode("utf-8", "backslashreplace").decode("utf-8")


def on_worker(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    if r.get("session"):
        j["session"] = r["session"]
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
    gitops.push_branch(wt, c.branch(job))
    j.update(status="needs_gate", head=gitops.rev(wt, "HEAD"))
    run.event(c.st, f"{job}: worker done, head {j['head'][:8]} pushed")


def on_gate(c: Ctx, job: str, r: dict) -> None:
    ok = r["exit"] == 0 and not r["reason"]
    tail = (r["result"] or "")[-2000:]
    merge.record(c.dir, job, "gate", {"ok": ok, "head": r["meta"].get("head"), "exit": r["exit"], "tail": tail})
    if ok:
        c.st["jobs"][job]["status"] = "needs_pr"
        run.event(c.st, f"{job}: job gate green")
    else:
        fail(c, job, f"job gate red (exit {r['exit']}{', ' + r['reason'] if r['reason'] else ''}): {tail[-600:]}")


def _review_answer(r: dict) -> tuple[str, list] | None:
    """(verdict, findings) from an answer of exactly the agreed shape; None for anything else."""
    d = brief.parse_json_line(r["result"]) if _clean_exit(r) else None
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
    out = None if r["reason"] else brief.parse_json_line(r["result"])
    if not isinstance(out, dict) or "ok" not in out:  # a stop or a death: nothing was learned about the job
        why = r["reason"] or "ark-integrate gave no result"
        if _integrate_again(c, job, "integrate_lost") >= 2:
            return block(c, job, f"ark-integrate ended twice without a result: {why}")
        run.event(c.st, f"{job}: ark-integrate ended without a result ({why}); checking again")
        return
    ok = out["ok"] is True and isinstance(out.get("base"), str)
    reason = out.get("reason", "")
    if not ok and out.get("base") is None:  # the check never ran: not the worker's doing
        n = _integrate_again(c, job, "integrate_skips")
        if n == 1:
            run.event(c.st, f"{job}: the integration check could not run: {reason}")
        if n == ERROR_ALERT_AFTER:
            run.alert(c.st, f"{job}: the integration check could not run for {n} cycles: {reason}")
        return
    j["integrate_lost"] = j["integrate_skips"] = 0
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
    d = brief.parse_json_line(r["result"]) if _clean_exit(r) else None
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


HANDLERS = {"worker": on_worker, "gate": on_gate, "review": on_review, "integrate": on_integrate, "judge": on_judge}


def _usd(value) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return value if ok else 0


def consume_receipts(c: Ctx) -> None:
    proc.reap(c.dir)
    for name in proc.finished(c.dir):
        r = proc.consume(c.dir, name)
        if r is None:
            continue
        job, _, kind = name.rpartition(".")
        j = c.st["jobs"].get(job)
        if j is None or kind not in HANDLERS or RUNNING.get(j["status"]) != kind:
            run.event(c.st, f"ignored the result of {name}: nothing was waiting for it")
            continue
        j["usd"] = round(j["usd"] + _usd(r.get("usd")), 4)
        if r.get("survivors"):
            run.alert(c.st, f"{job}: processes of its {kind} outlived it ({r['reason'] or 'it ended'}): "
                            "look for leftovers before they touch its worktree")
        if r["reason"] == "stopped by Marco":
            block(c, job, "stopped by Marco")
            continue
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
    if not wt.exists():
        gitops.fetch(c.repo)
        gitops.add_worktree(c.repo, wt, c.branch(job), f"origin/{c.base}")
        j["base"] = gitops.rev(wt, "HEAD")
    pre = [] if j["setup_done"] else list(c.plan.manifest.get("setup") or [])
    (c.dir / f"{job}.doneblocks").unlink(missing_ok=True)
    report = c.report(job)
    resume = bool(j["session"] and j["resume_msg"])
    if resume:
        prompt = j["resume_msg"]
    else:
        j["session"] = str(uuid.uuid4())
        prompt = brief.worker_brief(c.st, job, mj, c.plan, worktree=wt, report=report,
                                    note=j["note"] or j["resume_msg"], previous_report=c.read_report(job))
    env = claude.runner_env("worker", origin_url=gitops.origin_url(c.repo), worktree=wt, area=json.dumps(mj["area"]),
                            report=report, gate=mj["gate"], run_dir=c.dir, job=job)
    cmd = claude.agent_cmd("worker", claude.MODELS[j["tier"]], prompt, session=j["session"], resume=resume,
                           usd=mj["budget"]["usd"])
    proc.start(c.dir, f"{job}.worker", cmd, cwd=wt, env=env, minutes=claude.TIER_MINUTES[j["tier"]], pre=pre)
    j.update(status="working", attempts=j["attempts"] + 1, resume_msg=None, note=None, error=None)
    run.event(c.st, f"{job}: worker {j['tier']} attempt {j['attempts']}{' (resumed)' if resume else ''}")
    return True


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
    cmd = [str(paths.code_dir() / "bin" / "ark-integrate"), f"{c.st['repo']}/{c.st['slug']}", job, j["head"]]
    env = claude.runner_env("integrate", origin_url=gitops.origin_url(c.repo))  # it runs tests a worker wrote
    # Above the check's own timeout, or a slow gate dies here with no result instead of red with its reason.
    proc.start(c.dir, f"{job}.integrate", cmd, cwd=c.repo, env=env,
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
    if s == "pending":
        deps = [c.st["jobs"][d]["status"] for d in c.mjobs[job].get("after") or []]
        if "blocked" in deps:
            return lambda: block(c, job, "a job it depends on is blocked")
        if all(d == "merged" for d in deps) and slots.writer(repo):
            return lambda: _start_writer(c, job, slots)
    elif s == "needs_worker":
        if slots.writer(repo):
            return lambda: _start_writer(c, job, slots)
    elif s == "needs_gate":
        if slots.gate(j["heavy"]):
            return lambda: start_gate(c, job)
    elif s == "needs_integrate":
        if slots.gate(j["heavy"]):
            return lambda: start_integrate(c, job)
    else:
        step_ = {"needs_pr": ensure_pr, "needs_review": start_review, "needs_merge": do_merge,
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
    for job, j in st["jobs"].items():
        s = j["status"]
        if s in RUNNING:
            kind = RUNNING[s]
            if not (d / f"{job}.{kind}.proc").exists():
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
    if st["status"] == "waiting" and not try_start(st, states):
        return
    d = paths.run_dir(st["repo"], st["slug"])
    if not plan_ok(st):
        if st["status"] != "held":
            st["status"] = "held"
            run.alert(st, "the plan changed after its approval: nothing new is dispatched. Running "
                          "processes finish; their results wait until the approved plan is back.")
        proc.reap(d)
        return
    if st["status"] == "held":
        st["status"] = "running"
        run.event(st, "the approved plan is back: running again")
    c = Ctx(st, forge)
    consume_receipts(c)
    schedule(c, slots)


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


def cycle(forge=None) -> tuple[str, list[str], bool]:
    """One pass. Returns (status line, alerts to show, whether any run state changed)."""
    forge = forge or forge_mod.get()
    states, before, alerts = {}, {}, []
    for a in approvals():
        key = (a["repo"], a["slug"])
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
    for st in states.values():
        _guarded(st, "reconcile_error", lambda st=st: reconcile(st))
    slots = Slots(states.values(), paths.load_workspace()["limits"])
    for st in states.values():
        _guarded(st, "error", lambda st=st: step(st, list(states.values()), forge, slots))
    changed = False
    for key, st in states.items():
        alerts += st["alerts"]
        st["alerts"] = []
        if json.dumps(st, sort_keys=True) != before[key]:
            changed = True
            try:
                run.save(st)
                _said.discard((str(paths.ark_home()), "unsaved", *key))
            except Exception as e:  # the next cycle reloads the last saved state and reconcile() catches up
                _once(("unsaved", *key), f"{key[0]}/{key[1]}: the run state cannot be saved: "
                                         f"{type(e).__name__}: {e}", alerts)
    return status_line(list(states.values())), alerts, changed
