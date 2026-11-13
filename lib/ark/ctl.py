"""Marco's commands over running plans (spec §6). They read the run state and never write it: ark-loop is its only
writer, so anything that changes a run goes through the inbox or through a process's own stop record."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from . import accounts, claude, daycap, desk, gitops, inbox, paths, proc, questions, run, usage

FOLLOW_POLL = 1.0   # seconds between looks at a log being followed
ATTACH_WAIT = 60  # seconds for ark-loop to hand a job over
_sleep = time.sleep  # a test replaces this to run the loop while a command waits for it


class CtlError(Exception):
    """What Marco reads on stderr; the command exits 1."""


def _cmd(fn):
    def wrapped(args) -> int:
        try:
            return fn(args)
        except CtlError as e:
            print(e, file=sys.stderr)
            return 1
    return wrapped


def _writer() -> None:
    if "ARK_ROLE" in os.environ:
        raise CtlError("this command does not run inside a runner process.")


def _states() -> list[dict]:
    out, root = [], paths.runs_dir()
    for f in sorted(root.glob("**/state.json")):
        rel = f.parent.relative_to(root)
        try:
            st = run.load(str(rel.parent), rel.name)
        except run.StateCorrupt as e:
            print(f"warning: {e}", file=sys.stderr)
            continue
        if st:
            out.append(st)
    return out


def _live(st: dict) -> bool:
    return st["status"] not in ("done", "blocked")


def _resolve(spec: str) -> tuple[str, str, str]:
    """(repo, slug, job) from `<job>`, among live runs, or `<repo>/<slug>:<job>`, among all of them."""
    if ":" in spec:
        key, _, job = spec.rpartition(":")
        for st in _states():
            if f"{st['repo']}/{st['slug']}" == key and job in st["jobs"]:
                return st["repo"], st["slug"], job
        raise CtlError(f"{spec} does not exist")
    found = [st for st in _states() if _live(st) and spec in st["jobs"]]
    if not found:
        raise CtlError(f"no live run has the job {spec}; for a finished one use <repo>/<slug>:{spec}")
    if len(found) > 1:
        names = ", ".join(f"{st['repo']}/{st['slug']}:{spec}" for st in found)
        raise CtlError(f"{spec} is in several runs: {names}")
    return found[0]["repo"], found[0]["slug"], spec


def _age(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}"


def _ps_row(st: dict, job: str, j: dict) -> list[str]:
    d = paths.run_dir(st["repo"], st["slug"])
    infos = proc.job_processes(d, job)
    elapsed, tokens, last = "-", "-", ""
    if infos:
        info = next((i for i in infos if i["name"].endswith(".worker")), infos[0])  # a tick may be looking at it
        if isinstance(info.get("start"), (int, float)):
            elapsed = _age(time.time() - info["start"])
        seen = usage.scan(d / f"{info['name']}.log")
        tokens, last = f"{seen['tokens'] / 1000:.1f}k", seen["last"]
    if j.get("question"):
        last = j["question"]
    if j["status"] == "waiting_dep":
        last = f"waiting on {j['waiting_on']}"
    if j["status"] == "waiting_fix":
        last = f"waiting for base fix {j.get('fix', '?')}"
    last = " ".join(usage.clean(last).split())[:100]
    return [f"{st['repo']}/{st['slug']}", job, j["status"], j.get("tier", "?"), elapsed, tokens, f"${j.get('usd', 0):.2f}", last]


def _ps_header(st, now) -> str:
    elapsed, eta = desk._times(st["repo"], st["slug"], now)
    return f"{st['repo']}/{st['slug']} · {st['status']} · elapsed {elapsed} · eta {eta}"


@_cmd
def cmd_ps(args) -> int:
    states = [st for st in _states() if _live(st)]
    runs = [(st, [_ps_row(st, job, j) for job, j in st["jobs"].items() if j["status"] not in run.TERMINAL])
            for st in states]
    runs = [(st, rows) for st, rows in runs if rows]
    capq = _open_cap_questions()
    for q in capq:
        print(f"💸 account {q['account']} at its daily cap · {q['id']}: ark answer")
    if not runs:
        if not capq:
            print("nothing running")
        return 0
    head = ["RUN", "JOB", "STATUS", "TIER", "TIME", "TOKENS", "USD", "LAST MESSAGE"]
    widths = [max(len(r[i]) for r in [head, *(r for _, rows in runs for r in rows)]) for i in range(len(head) - 1)]
    now = datetime.now(timezone.utc)
    for st, rows in runs:
        print(_ps_header(st, now))
        for r in [head, *rows]:
            print("  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1])
    waiting = sum(len(st.get("questions") or []) for st in states) + len(capq)
    if waiting:
        print(f"❓ {waiting}: ark answer")
    return 0


def _log_file(d, name: str):
    live = d / f"{name}.log"
    if live.is_file():
        return live

    def stamp(p):
        tail = p.stem.rpartition("-")[2]
        return int(tail) if tail.isdigit() else 0
    archived = sorted((d / "done").glob(f"{name}-*.log"), key=stamp)
    return archived[-1] if archived else None


def _print_rendered(data: bytes) -> None:
    for raw in data.split(b"\n"):
        text = usage.render_line(raw) if raw.strip() else None
        if text:
            print(text, flush=True)


@_cmd
def cmd_logs(args) -> int:
    repo, slug, job = _resolve(args.job)
    d = paths.run_dir(repo, slug)
    name = f"{job}.{args.kind}"
    log = _log_file(d, name)
    if log is None:
        raise CtlError(f"{name} has no log")
    data, offset = usage.read_from(log)
    cut = data.rfind(b"\n") + 1 if args.follow else len(data)   # following: a half-written line waits
    _print_rendered(data[:cut])
    offset -= len(data) - cut
    while args.follow and name in proc.active(d) and not (d / f"{name}.receipt.json").exists():
        _sleep(FOLLOW_POLL)
        data, _ = usage.read_from(log, offset)
        cut = data.rfind(b"\n") + 1
        _print_rendered(data[:cut])
        offset += cut
    return 0


@_cmd
def cmd_stop(args) -> int:
    _writer()
    repo, slug, job = _resolve(args.job)
    d = paths.run_dir(repo, slug)
    if any(n.startswith(job + ".") for n in proc.active(d)):
        return proc._stop_cli(d, job)
    status = run.load(repo, slug)["jobs"][job]["status"]
    if status in run.TERMINAL:
        raise CtlError(f"{job} already finished: {status}")
    inbox.post(repo, slug, "stop", job=job)  # between processes: in a backoff, or waiting for Marco
    print(f"{job} has no process: it gets blocked on the next cycle")
    return 0


@_cmd
def cmd_retry(args) -> int:
    _writer()
    repo, slug, job = _resolve(args.job)
    j = run.load(repo, slug)["jobs"][job]
    if j["status"] != "blocked":
        raise CtlError(f"{job} is not blocked: {j['status']}")
    inbox.post(repo, slug, "retry", job=job)
    print(f"{job} goes back to its step on the next cycle")
    return 0


def _posted_answers(st: dict) -> set:
    return {i.get("id") for i in inbox.pending(st["repo"], st["slug"]) if i["kind"] == "answer"}


def _open_questions() -> list[tuple[dict, dict]]:
    out = []
    for st in _states():
        if _live(st):
            posted = _posted_answers(st)
            out += [(st, q) for q in st.get("questions") or [] if q["id"] not in posted]
    return out


def _show(st: dict, q: dict) -> None:
    job = f" · job {q['job']}" if q["job"] else ""
    print(f"{st['repo']}/{st['slug']} · {q['id']}{job}\nplan: {st['plan']}\n\n{q['text']}\n")


def _post_answer(st: dict, q: dict, text: str) -> None:
    problem = questions.check_answer(q, text)
    if problem:
        raise CtlError(f"answer rejected: {problem}")
    inbox.post(st["repo"], st["slug"], "answer", id=q["id"], text=text)
    print(f"{q['id']} noted: ark-loop applies it on the next cycle")


def _open_cap_questions() -> list[dict]:
    """Account questions (daycap.py) not answered yet in the inbox."""
    posted = {i.get("id") for i in daycap.pending()}
    return [q for q in daycap.load()["questions"] if q["id"] not in posted]


def _show_cap(q: dict) -> None:
    print(f"account {q['account']} · {q['id']}\n\n{q['text']}\n")


def _post_cap(q: dict, text: str) -> None:
    problem = daycap.check_answer(text)
    if problem:
        raise CtlError(f"answer rejected: {problem}")
    daycap.post(q["id"], text)
    print(f"{q['id']} noted: ark-loop applies it on the next cycle")


def _answer_cap(qid: str, text: str) -> int:
    q = next((q for q in _open_cap_questions() if q["id"] == qid), None)
    if q is None:
        raise CtlError(f"no open account question {qid}")
    _post_cap(q, text)
    return 0


def _answer_by_id(key: str, qid: str, text: str) -> int:
    st = next((s for s in _states() if f"{s['repo']}/{s['slug']}" == key and _live(s)), None)
    if st is None:
        raise CtlError(f"no live run {key}")
    q = questions.find(st, qid)
    if q is None:
        raise CtlError(f"{key} has no open question {qid}")
    _post_answer(st, q, text)
    return 0


@_cmd
def cmd_answer(args) -> int:
    if args.list:
        capq, found = _open_cap_questions(), _open_questions()
        for q in capq:
            _show_cap(q)
        for st, q in found:
            _show(st, q)
        if not capq and not found:
            print("no open questions")
        return 0
    _writer()
    if args.cap:
        return _answer_cap(*args.cap)
    if args.run:
        return _answer_by_id(*args.run)
    capq, found = _open_cap_questions(), _open_questions()
    if not capq and not found:
        print("no open questions")
    for q in capq:  # first: each one holds every run of its account
        _show_cap(q)
        try:
            text = input("> ").strip()
        except EOFError:
            return 0
        if not text:  # skipped: it stays open
            continue
        try:
            _post_cap(q, text)
        except CtlError as e:
            print(e)
    for st, q in found:
        _show(st, q)
        try:
            text = input("> ").strip()
        except EOFError:
            break
        if not text:  # skipped: it stays open
            continue
        try:
            _post_answer(st, q, text)
        except CtlError as e:
            print(e)
    return 0


@_cmd
def cmd_steer(args) -> int:
    _writer()
    repo, slug, job = _resolve(args.job)
    text = args.text.strip()
    if not text:
        raise CtlError("the instruction is empty")
    status = run.load(repo, slug)["jobs"][job]["status"]
    if status != "working":
        raise CtlError(f"{job} is not working: {status}")
    inbox.post(repo, slug, "steer", job=job, text=text)
    print(f"{job}: noted; ark-loop stops it and resumes its session with the instruction")
    return 0


def _wait_attached(repo: str, slug: str, job: str) -> dict:
    for _ in range(ATTACH_WAIT):
        j = run.load(repo, slug)["jobs"][job]
        if j["status"] == "attached":
            return j
        if j["status"] not in ("working", "waiting_answer"):
            raise CtlError(f"{job} moved to {j['status']}: it cannot be taken")
        _sleep(1)
    raise CtlError(f"ark-loop did not hand over {job} in {ATTACH_WAIT} s. Is it running? The request still stands.")


@_cmd
def cmd_attach(args) -> int:
    _writer()
    if not sys.stdin.isatty():
        raise CtlError("ark attach opens an interactive session: it needs a terminal")
    repo, slug, job = _resolve(args.job)
    st = run.load(repo, slug)
    if st["status"] != "running":
        raise CtlError(f"run {repo}/{slug} is {st['status']}: a job cannot be taken")
    status = st["jobs"][job]["status"]
    if status not in ("working", "waiting_answer", "attached"):
        raise CtlError(f"{job} cannot be taken: {status}")
    try:
        env = claude.with_account(dict(os.environ), repo)
    except accounts.AccountError as e:
        raise CtlError(f"cannot open the session of {args.job} with its account: {e}") from None
    if status != "attached":
        inbox.post(repo, slug, "attach", job=job)
    j = _wait_attached(repo, slug, job)
    wt = paths.worktrees_dir() / repo / slug / job
    subprocess.run([claude.claude_bin()] + (["--resume", j["session"]] if j["session"] else []), cwd=wt, env=env)
    if not gitops.is_clean(wt):
        raise CtlError(f"uncommitted changes left in {wt}. Commit and run `ark attach {args.job}` again; "
                       "the job is still in your hands.")
    inbox.post(repo, slug, "attach_done", job=job)
    print(f"{job} goes back to the runner: gate, review and merge")
    return 0


def register(sub) -> None:
    s = sub.add_parser("ps", help="what is running: job, time, tokens, last message")
    s.set_defaults(fn=cmd_ps)
    s = sub.add_parser("logs", help="a job's log, readable")
    s.add_argument("job", help="<job> or <repo>/<slug>:<job>")
    s.add_argument("--kind", default="worker", help="worker (default), gate, review, euklides, intervene")
    s.add_argument("-f", "--follow", action="store_true", help="follow it while it runs")
    s.set_defaults(fn=cmd_logs)
    s = sub.add_parser("stop", help="stop a job: it ends blocked")
    s.add_argument("job", help="<job> or <repo>/<slug>:<job>")
    s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("retry", help="put a blocked job back on the step where it stopped")
    s.add_argument("job", help="<job> or <repo>/<slug>:<job> (a closed run needs the long form)")
    s.set_defaults(fn=cmd_retry)
    s = sub.add_parser("answer", help="answer the open questions, one by one")
    s.add_argument("--list", action="store_true", help="show them without answering")
    which = s.add_mutually_exclusive_group()
    which.add_argument("--run", nargs=3, metavar=("REPO/SLUG", "ID", "TEXT"), help="answer just one")
    which.add_argument("--cap", nargs=2, metavar=("ID", "TEXT"), help="answer an account's daily cap question")
    s.set_defaults(fn=cmd_answer)
    s = sub.add_parser("steer", help="stop a worker and resume its session with an instruction")
    s.add_argument("job", help="<job> or <repo>/<slug>:<job>")
    s.add_argument("text", help="the instruction")
    s.set_defaults(fn=cmd_steer)
    s = sub.add_parser("attach", help="take a worker into an interactive session; on exit it goes back to the runner")
    s.add_argument("job", help="<job> or <repo>/<slug>:<job>")
    s.set_defaults(fn=cmd_attach)
