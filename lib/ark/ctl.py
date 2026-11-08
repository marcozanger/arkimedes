"""Marco's commands over running plans (spec §6). They read the run state and never write it: ark-loop is its only
writer, so anything that changes a run goes through the inbox or through a process's own stop record."""
from __future__ import annotations

import os
import subprocess
import sys
import time

from . import accounts, claude, gitops, inbox, paths, proc, questions, run, usage

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
        raise CtlError("este comando no corre dentro de un proceso del runner.")


def _states() -> list[dict]:
    out, root = [], paths.runs_dir()
    for f in sorted(root.glob("**/state.json")):
        rel = f.parent.relative_to(root)
        try:
            st = run.load(str(rel.parent), rel.name)
        except run.StateCorrupt as e:
            print(f"ojo: {e}", file=sys.stderr)
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
        raise CtlError(f"no existe {spec}")
    found = [st for st in _states() if _live(st) and spec in st["jobs"]]
    if not found:
        raise CtlError(f"ningún run vivo tiene el job {spec}; para uno terminado usá <repo>/<slug>:{spec}")
    if len(found) > 1:
        names = ", ".join(f"{st['repo']}/{st['slug']}:{spec}" for st in found)
        raise CtlError(f"{spec} está en varios runs: {names}")
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
    last = " ".join(usage.clean(last).split())[:100]
    return [f"{st['repo']}/{st['slug']}", job, j["status"], j.get("tier", "?"), elapsed, tokens, f"${j.get('usd', 0):.2f}", last]


@_cmd
def cmd_ps(args) -> int:
    states = [st for st in _states() if _live(st)]
    rows = [_ps_row(st, job, j) for st in states for job, j in st["jobs"].items() if j["status"] not in run.TERMINAL]
    if not rows:
        print("nada corriendo")
        return 0
    head = ["RUN", "JOB", "ESTADO", "TIER", "TIEMPO", "TOKENS", "USD", "ÚLTIMO MENSAJE"]
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head) - 1)]
    for r in [head, *rows]:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1])
    waiting = sum(len(st.get("questions") or []) for st in states)
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
        raise CtlError(f"{name} no tiene log")
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
        raise CtlError(f"{job} ya terminó: {status}")
    inbox.post(repo, slug, "stop", job=job)  # between processes: in a backoff, or waiting for Marco
    print(f"{job} no tiene ningún proceso: queda bloqueado en el próximo ciclo")
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
        raise CtlError(f"respuesta rechazada: {problem}")
    inbox.post(st["repo"], st["slug"], "answer", id=q["id"], text=text)
    print(f"{q['id']} anotada: ark-loop la aplica en el próximo ciclo")


def _answer_by_id(key: str, qid: str, text: str) -> int:
    st = next((s for s in _states() if f"{s['repo']}/{s['slug']}" == key and _live(s)), None)
    if st is None:
        raise CtlError(f"no hay un run vivo {key}")
    q = questions.find(st, qid)
    if q is None:
        raise CtlError(f"{key} no tiene la pregunta abierta {qid}")
    _post_answer(st, q, text)
    return 0


@_cmd
def cmd_answer(args) -> int:
    if args.list:
        found = _open_questions()
        for st, q in found:
            _show(st, q)
        if not found:
            print("no hay preguntas abiertas")
        return 0
    _writer()
    if args.run:
        return _answer_by_id(*args.run)
    found = _open_questions()
    if not found:
        print("no hay preguntas abiertas")
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
        raise CtlError("la instrucción está vacía")
    status = run.load(repo, slug)["jobs"][job]["status"]
    if status != "working":
        raise CtlError(f"{job} no está trabajando: {status}")
    inbox.post(repo, slug, "steer", job=job, text=text)
    print(f"{job}: anotado; ark-loop lo detiene y retoma su sesión con la instrucción")
    return 0


def _wait_attached(repo: str, slug: str, job: str) -> dict:
    for _ in range(ATTACH_WAIT):
        j = run.load(repo, slug)["jobs"][job]
        if j["status"] == "attached":
            return j
        if j["status"] not in ("working", "waiting_answer"):
            raise CtlError(f"{job} pasó a {j['status']}: no se puede tomar")
        _sleep(1)
    raise CtlError(f"ark-loop no entregó {job} en {ATTACH_WAIT} s. ¿Está corriendo? El pedido sigue en pie.")


@_cmd
def cmd_attach(args) -> int:
    _writer()
    if not sys.stdin.isatty():
        raise CtlError("ark attach abre una sesión interactiva: necesita una terminal")
    repo, slug, job = _resolve(args.job)
    st = run.load(repo, slug)
    if st["status"] != "running":
        raise CtlError(f"el run {repo}/{slug} está {st['status']}: no se puede tomar un job")
    status = st["jobs"][job]["status"]
    if status not in ("working", "waiting_answer", "attached"):
        raise CtlError(f"{job} no se puede tomar: {status}")
    try:
        env = claude.with_account(dict(os.environ), repo)
    except accounts.AccountError as e:
        raise CtlError(f"no puedo abrir la sesión de {args.job} con su cuenta: {e}") from None
    if status != "attached":
        inbox.post(repo, slug, "attach", job=job)
    j = _wait_attached(repo, slug, job)
    wt = paths.worktrees_dir() / repo / slug / job
    subprocess.run([claude.claude_bin()] + (["--resume", j["session"]] if j["session"] else []), cwd=wt, env=env)
    if not gitops.is_clean(wt):
        raise CtlError(f"quedaron cambios sin commitear en {wt}. Commiteá y corré `ark attach {args.job}` otra vez; "
                       "el job sigue en tus manos.")
    inbox.post(repo, slug, "attach_done", job=job)
    print(f"{job} vuelve al runner: gate, review y merge")
    return 0


def register(sub) -> None:
    s = sub.add_parser("ps", help="qué corre: job, tiempo, tokens, último mensaje")
    s.set_defaults(fn=cmd_ps)
    s = sub.add_parser("logs", help="el log de un job, legible")
    s.add_argument("job", help="<job> o <repo>/<slug>:<job>")
    s.add_argument("--kind", default="worker", help="worker (default), gate, review, judge, intervene")
    s.add_argument("-f", "--follow", action="store_true", help="seguirlo mientras corre")
    s.set_defaults(fn=cmd_logs)
    s = sub.add_parser("stop", help="detener un job: queda bloqueado")
    s.add_argument("job", help="<job> o <repo>/<slug>:<job>")
    s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("answer", help="responder las preguntas abiertas, una por una")
    s.add_argument("--list", action="store_true", help="mostrarlas sin responder")
    s.add_argument("--run", nargs=3, metavar=("REPO/SLUG", "ID", "TEXTO"), help="responder una sola")
    s.set_defaults(fn=cmd_answer)
    s = sub.add_parser("steer", help="detener un worker y retomar su sesión con una instrucción")
    s.add_argument("job", help="<job> o <repo>/<slug>:<job>")
    s.add_argument("text", help="la instrucción")
    s.set_defaults(fn=cmd_steer)
    s = sub.add_parser("attach", help="tomar un worker en una sesión interactiva; al salir vuelve al runner")
    s.add_argument("job", help="<job> o <repo>/<slug>:<job>")
    s.set_defaults(fn=cmd_attach)
