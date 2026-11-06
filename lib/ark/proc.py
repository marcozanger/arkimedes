"""Detached processes of a run: workers, reviewers, gates, integration checks, ticks.

Each process `name` in a run folder has:
  <name>.proc          pid, wrapper identity, command, start, deadline, meta (written at start)
  <name>.log           its stdout and stderr
  <name>.receipt.json  written when it ends: exit, reason, result, usd, session
  <name>.stop          Marco stopped it: written before any signal, and it decides the receipt
ark-loop consumes receipts (they move to done/ with the log). A process that died without
a receipt, or ran past its deadline, gets one from reap(). `ark-dispatch stop` stops one by hand.

A stop from Marco wins over whoever else has a word on how the process ended: the loop can see the
wrapper gone and reap it while the stop waits for the group, and the process can end on its own just
before the signal. The mark holds until the loop has the result safely on disk (forget_stop).

The wrapper leads its own process group. Three rules keep signals where they belong:
- A pid alone proves nothing after a restart: the runner signals a group only while its leader is
  the wrapper it started, same start time and same command as `.proc` recorded. A reused pid means
  the wrapper died. If the identity cannot be read, nothing is signalled and nothing is decided.
- Once the wrapper is gone nobody outside the group can tell whose group it is. So a small watcher
  forked inside the group sweeps it from within when the wrapper ends, however it ends, and
  escalates to SIGKILL. That covers a killed wrapper, a stop, and background jobs left behind.
- The wrapper starts nothing until `.proc` is on disk, so no running command is ever unrecorded.
"""
from __future__ import annotations

import calendar
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import paths

TERM_GRACE = 3.0                      # seconds the watcher gives a group between SIGTERM and SIGKILL
KILL_WAIT = TERM_GRACE + 1.0          # longest a cycle waits for one group to end
START_SLOP = 2                        # ps start times can move a second on Linux

STOPPED_BY_MARCO = "stopped by Marco"

OURS, GONE, REUSED, UNKNOWN = "ours", "gone", "reused", "unknown"

# ps must answer the same whatever the caller's locale, timezone or PATH. Dates stay in English
# (LC_TIME=C) but the command comes out as UTF-8: with LC_ALL=C, ps prints non-ASCII bytes as `M-CM-1`
# and a run path like `diseño` would never match what .proc recorded.
PS_ENV = {"LC_CTYPE": "en_US.UTF-8", "LC_TIME": "C", "TZ": "UTC", "PATH": "/bin:/usr/bin"}
LSOF_ENV = {"LC_CTYPE": "en_US.UTF-8", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}

# Popen objects of the processes this ark-loop started: poll() them so none stays a zombie.
_children: dict[str, subprocess.Popen] = {}


def _atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _valid_proc(d: dict) -> bool:
    pid, cmd = d.get("pid"), d.get("leader_cmd")
    numbers = (d.get("deadline"), d.get("leader_start"))
    return (type(pid) is int and pid > 1 and isinstance(cmd, str) and bool(cmd)
            and all(isinstance(n, (int, float)) and math.isfinite(n) for n in numbers)
            and isinstance(d.get("meta", {}), dict))


def _read(path: Path) -> dict | None:
    """A JSON object, or None. A `.proc` must also hold everything a signal would rely on."""
    try:
        d = json.loads(paths.read_regular(path).decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or (path.suffix == ".proc" and not _valid_proc(d)):
        return None
    return d


def parse_output(text: str) -> dict:
    """The last `{"type": "result"}` line of claude's json / stream-json output. For any other
    program, the tail of its output. With no result line (killed, crashed, never reached the model), what
    the other lines tell: its session, and `spoke`, whether anything but claude's own start-up was logged."""
    session, spoke = None, False
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("type") == "result":
            return {"result": str(d.get("result") or ""), "usd": d.get("total_cost_usd"),
                    "session": d.get("session_id"), "is_error": bool(d.get("is_error"))}
        spoke = spoke or d.get("type") != "system"
        if session is None and isinstance(d.get("session_id"), str):
            session = d["session_id"]
    return {"result": text[-4000:], "usd": None, "session": session, "is_error": None, "spoke": spoke}


def _mark(run_dir: Path, name: str) -> Path:
    return run_dir / f"{name}.stop"


def stop_requested(run_dir, name: str) -> bool:
    """Did Marco stop this process? The mark names the pid it was for, so an older process's mark says
    nothing about a newer one. With no readable `.proc` to compare, the mark counts: it fails towards stopping."""
    run_dir = Path(run_dir)
    mark = _read(_mark(run_dir, name))
    info = _read(run_dir / f"{name}.proc")
    return mark is not None and (info is None or mark.get("pid") == info["pid"])


def forget_stop(run_dir, name: str) -> None:
    """The loop calls this once the stop's result is on disk; until then a crash must not lose it."""
    _mark(Path(run_dir), name).unlink(missing_ok=True)


def _note_stop(run_dir: Path, name: str) -> bool:
    """Record Marco's stop before anything can end the process. False if the process was consumed meanwhile:
    then it is over, the job has moved on and the mark must not stay behind to speak for a later process."""
    record = run_dir / f"{name}.proc"
    info = _read(record)
    pid = info["pid"] if info else None
    try:
        _atomic(_mark(run_dir, name), {"pid": pid, "at": time.time()})
    except OSError:
        pass                                              # the signal matters more; stop's own receipt still says it
    now = _read(record)
    if not os.path.lexists(record) or (now["pid"] if now else None) != pid:
        forget_stop(run_dir, name)
        return False
    return True


def _receipt(run_dir: Path, name: str, code: int, reason: str, **extra) -> None:
    """Write the receipt unless there is one: the first word on how a process ended stands, except
    that a stop from Marco says who ended it (what else was seen stays in `cause`)."""
    path = run_dir / f"{name}.receipt.json"
    if path.exists():
        return
    if reason != STOPPED_BY_MARCO and stop_requested(run_dir, name):
        code, reason, extra = -15, STOPPED_BY_MARCO, {**extra, "cause": reason}
    log = run_dir / f"{name}.log"
    try:
        text = paths.read_regular(log).decode("utf-8", "replace")
    except OSError:
        text = ""
    _atomic(path, {"name": name, "exit": code, "reason": reason, "end": time.time(), **parse_output(text), **extra})


def _ps(*args: str) -> str | None:
    """ps's output, or None if it could not answer. No output means no such process."""
    try:
        r = subprocess.run(["ps", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                           env=PS_ENV,
                           stdin=subprocess.DEVNULL, start_new_session=True)   # never a member of a group we watch
    except (OSError, subprocess.SubprocessError):
        return None
    return None if not r.stdout.strip() and r.stderr.strip() else r.stdout


def _describe(pid: int) -> tuple[float, str] | str:
    """(start time, command) of the live process `pid`, or GONE (a zombie counts), or UNKNOWN."""
    out = _ps("-ww", "-o", "stat=", "-o", "lstart=", "-o", "command=", "-p", str(pid))
    if out is None:
        return UNKNOWN
    parts = out.split(None, 6)
    if not parts or parts[0].startswith("Z"):
        return GONE
    try:
        start = calendar.timegm(time.strptime(" ".join(parts[1:6]), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return UNKNOWN
    return start, parts[6] if len(parts) > 6 else ""


def _group_members(pgid: int) -> set[int] | None:
    """Pids of the group's live members (zombies do not count), or None if ps could not answer."""
    out = _ps("-A", "-o", "pid=", "-o", "pgid=", "-o", "stat=")
    if out is None:
        return None
    members = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == str(pgid) and not parts[2].startswith("Z"):
            members.add(int(parts[0]))
    return members


def _group_ended(pgid: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while True:
        if _group_members(pgid) == set():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)


def _leader_state(run_dir: Path, name: str, info: dict) -> str:
    """Is the recorded wrapper still the process at that pid? OURS is the only answer that allows a signal."""
    child = _children.get(str(run_dir / name))
    if child is not None and child.pid == info["pid"]:
        return OURS if child.poll() is None else GONE     # unreaped child: its pid cannot have been reused
    seen = _describe(info["pid"])
    if isinstance(seen, str):
        return seen
    start, command = seen
    same = abs(start - info["leader_start"]) <= START_SLOP and info["leader_cmd"] in command
    return OURS if same else REUSED


def _sweep_group_when_wrapper_ends(wrapper_end: int) -> None:
    """The watcher: blocks until the wrapper is gone, then ends the rest of its own group."""
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, signal.SIG_IGN)                # a stop must not take the watcher first
    os.read(wrapper_end, 1)                               # returns when the wrapper's copy closes
    me, group = os.getpid(), os.getpgrp()
    os.killpg(group, signal.SIGTERM)
    end = time.monotonic() + TERM_GRACE
    while time.monotonic() < end:
        members = _group_members(group)
        if members is not None and not members - {me}:
            return
        time.sleep(0.05)
    os.killpg(group, signal.SIGKILL)


def _start_watcher() -> None:
    """Fork the watcher. The write end stays open in this process only (pipes do not survive exec),
    so its end of file means the wrapper is gone."""
    read_end, write_end = os.pipe()
    if os.fork() == 0:
        try:
            os.close(write_end)
            _sweep_group_when_wrapper_ends(read_end)
        finally:
            os._exit(0)
    os.close(read_end)


def _wrapper(run_dir: Path, name: str, cwd: str, pre: list[str], cmd: list[str]) -> int:
    if not sys.stdin.buffer.read(1):                      # the starter did not record this process
        return 0
    # PYTHONPATH was set only so this wrapper could import ark; the command gets the original.
    orig = os.environ.pop("ARK_ORIG_PYTHONPATH", None)
    os.environ.pop("PYTHONPATH", None)
    if orig:
        os.environ["PYTHONPATH"] = orig
    code, reason = 0, ""
    try:
        if os.getpgrp() == os.getpid():                   # sweeping any other group is not ours to do
            _start_watcher()
    except OSError as e:
        code, reason = 127, f"cannot start: {e}"
    log_path = run_dir / f"{name}.log"
    log_path.unlink(missing_ok=True)  # never through what a worker planted there
    with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644), "wb") as log:
        try:
            for p in pre if not code else ():
                code = subprocess.call(["sh", "-c", p], cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
                if code:
                    reason = f"setup failed: {p}"
                    break
            if not code:
                code = subprocess.call(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        except OSError as e:
            code, reason = 127, f"cannot start: {e}"
    # Consumed or replaced meanwhile: this receipt would be read as the result of another process.
    record = run_dir / f"{name}.proc"
    info = _read(record)
    if record.exists() and (info is None or info["pid"] == os.getpid()):
        _receipt(run_dir, name, code, reason)
    return 0


def _leader_start(pid: int) -> float | None:
    for _ in range(3):
        seen = _describe(pid)
        if not isinstance(seen, str):
            return seen[0]
        time.sleep(0.1)
    return None


def start(run_dir, name: str, cmd: list[str], *, cwd, env: dict, minutes: float,
          pre=(), meta: dict | None = None) -> dict:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{name}.receipt.json").unlink(missing_ok=True)
    forget_stop(run_dir, name)
    env = dict(env)
    if env.get("PYTHONPATH"):
        env["ARK_ORIG_PYTHONPATH"] = env["PYTHONPATH"]
    env["PYTHONPATH"] = str(paths.code_dir() / "lib")
    argv = [sys.executable, "-m", "ark.proc", "run", str(run_dir), name, "--cwd", str(cwd)]
    for p in pre:
        argv += ["--pre", p]
    argv += ["--", *cmd]
    p = subprocess.Popen(argv, env=env, start_new_session=True, stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _children[str(run_dir / name)] = p
    leader_start = _leader_start(p.pid)                   # the wrapper is waiting on stdin: it is alive
    now = time.time()
    info = {"name": name, "pid": p.pid, "cmd": " ".join(cmd)[:300], "cwd": str(cwd), "start": now,
            "deadline": now + minutes * 60, "leader_start": leader_start or 0,
            "leader_cmd": f"-m ark.proc run {run_dir} {name} --cwd", "meta": meta or {}}
    _atomic(run_dir / f"{name}.proc", info)
    try:
        if leader_start is not None:
            p.stdin.write(b"go")
        p.stdin.close()
    except OSError:
        pass                                              # it died: reap() will give it a receipt
    if leader_start is None:                              # never runs: the end of file tells it not to
        _receipt(run_dir, name, -1, "cannot verify the process identity: ps did not answer")
    return info


def _alive(run_dir: Path, name: str, pid: int) -> bool:
    """False once it is known that the recorded wrapper is gone. Unknown counts as alive: no decision yet."""
    info = _read(run_dir / f"{name}.proc")
    if info is None or info["pid"] != pid:
        return False
    return _leader_state(run_dir, name, info) in (OURS, UNKNOWN)


def active(run_dir) -> list[str]:
    """Started and not consumed yet (running, or finished with a receipt waiting)."""
    return sorted(p.name.removesuffix(".proc") for p in Path(run_dir).glob("*.proc"))


def finished(run_dir) -> list[str]:
    return [n for n in active(run_dir) if (Path(run_dir) / f"{n}.receipt.json").exists()]


def overdue(run_dir) -> list[str]:
    run_dir, now = Path(run_dir), time.time()
    out = []
    for n in active(run_dir):
        info = _read(run_dir / f"{n}.proc")
        if info and now > info["deadline"] and not (run_dir / f"{n}.receipt.json").exists():
            out.append(n)
    return out


def _record_death(run_dir: Path, name: str, info: dict, state: str) -> None:
    """The wrapper is gone: no signal from here. Its watcher is ending the group, if the group is
    still ours; wait for that so a retry never runs next to the old process. A pid that was reused
    means the old group is over: the system does not hand out a pid whose group still exists."""
    if state == REUSED or _group_ended(info["pid"], KILL_WAIT):
        _receipt(run_dir, name, -1, "died without a receipt")
    else:
        _receipt(run_dir, name, -1, f"died without a receipt; process group {info['pid']} still has members "
                                    "that cannot be verified as ours: not signalled", survivors=True)


def reap(run_dir) -> list[str]:
    """Give a receipt to every process that died without one or ran past its deadline. A `.proc` that
    cannot be trusted gets a failure and no signal."""
    run_dir = Path(run_dir)
    done = []
    for n in active(run_dir):
        receipt = run_dir / f"{n}.receipt.json"
        if receipt.exists():
            continue
        info = _read(run_dir / f"{n}.proc")
        if info is None:
            _receipt(run_dir, n, -1, "unreadable .proc: not signalled, its process may still be running")
            done.append(n)
            continue
        state = _leader_state(run_dir, n, info)
        if state == UNKNOWN:
            continue
        if state != OURS:
            _record_death(run_dir, n, info, state)
            done.append(n)
        elif time.time() > info["deadline"]:
            stop(run_dir, n, "timeout: over its time limit")
            if receipt.exists():
                done.append(n)
    return done


def stop(run_dir, name: str, reason: str) -> bool:
    """True if the process was running and now is not. Takes up to KILL_WAIT + 1 seconds, and only that
    for a process that survives SIGTERM. A wrapper that is already gone gets its death recorded. A stop
    from Marco is on record first, so it holds even when this returns False (see stop_requested)."""
    run_dir = Path(run_dir)
    if reason == STOPPED_BY_MARCO and not _note_stop(run_dir, name):
        return False
    info = _read(run_dir / f"{name}.proc")
    if info is None or (run_dir / f"{name}.receipt.json").exists():
        return False
    state = _leader_state(run_dir, name, info)
    if state == UNKNOWN:
        return False
    if state != OURS:
        _record_death(run_dir, name, info, state)
        return False
    try:
        os.killpg(info["pid"], signal.SIGTERM)
    except OSError:
        pass
    ended = _group_ended(info["pid"], KILL_WAIT)          # the watcher escalates once the wrapper is gone
    if not ended and _leader_state(run_dir, name, info) == OURS:
        try:
            os.killpg(info["pid"], signal.SIGKILL)
        except OSError:
            pass
        ended = _group_ended(info["pid"], 1)
    _receipt(run_dir, name, -15, reason, **({} if ended else {"survivors": True}))
    return True


def consume(run_dir, name: str) -> dict | None:
    """Read a receipt and archive it (with its log) in done/; the process is forgotten. None if there is
    no receipt, and then nothing changes. A crash at any step is safe: the steps repeat with the same
    result and the same archive name until `.proc` goes. That unlink is the commit: without a `.proc`
    the process is no longer listed, and a receipt left behind is invisible until start() clears it."""
    run_dir = Path(run_dir)
    receipt = run_dir / f"{name}.receipt.json"
    if not receipt.exists():
        return None
    r = {"name": name, "exit": -1, "reason": "unreadable receipt", "result": "", "usd": None, "session": None,
         "is_error": None, "end": None, **(_read(receipt) or {})}
    info = _read(run_dir / f"{name}.proc") or {}
    r["meta"], r["start"] = info.get("meta", {}), info.get("start")
    done = run_dir / "done"
    done.mkdir(exist_ok=True)
    end = r["end"]
    if not (isinstance(end, (int, float)) and math.isfinite(end)):
        r.update(reason="unreadable receipt", exit=-1, end=None)
        end = None
    if r["reason"] != STOPPED_BY_MARCO and stop_requested(run_dir, name):  # it came after that receipt was written
        r.update(reason=STOPPED_BY_MARCO, exit=-15, cause=r["reason"])
    stamp = f"{name}-{int(end * 1e9) if end is not None else time.time_ns()}"
    _atomic(done / f"{stamp}.json", {**r, "proc": info})
    log = run_dir / f"{name}.log"
    if log.exists():
        log.replace(done / f"{stamp}.log")
    (run_dir / f"{name}.proc").unlink(missing_ok=True)
    receipt.unlink(missing_ok=True)
    child = _children.pop(str(run_dir / name), None)
    if child is not None:
        child.poll()
    return r


class CwdHoldersError(RuntimeError):
    """lsof could not answer: not the same as nobody being there."""


def cwd_holders(path) -> list[int]:
    """Pids of this user's processes whose current directory is `path` or inside it (symlinks resolved).
    Raises CwdHoldersError if lsof is missing, times out or fails: an empty list always means none."""
    root = os.path.realpath(path)
    try:
        r = subprocess.run(["lsof", "-nP", "-a", "-d", "cwd", "-u", str(os.getuid()), "-Fpn"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                           env=LSOF_ENV, stdin=subprocess.DEVNULL, start_new_session=True)
    except (OSError, subprocess.SubprocessError) as e:
        raise CwdHoldersError(f"lsof could not run: {e}") from e
    if r.returncode == 1 and not r.stdout.strip() and not r.stderr.strip():
        return []                                         # lsof's way of saying nothing matched
    if r.returncode not in (0, 1) or not r.stdout.strip() or (r.returncode == 1 and r.stderr.strip()):
        raise CwdHoldersError(f"lsof failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()[:200]}")
    holders, pid, seen = [], None, 0
    for line in r.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid, seen = int(line[1:]), seen + 1
        elif line.startswith("n") and pid is not None:
            cwd = os.path.realpath(line[1:])
            if cwd == root or cwd.startswith(root.rstrip(os.sep) + os.sep):
                holders.append(pid)
            pid = None
    if not seen:
        raise CwdHoldersError(f"lsof gave output that could not be read: {r.stdout.strip()[:200]}")
    return sorted(set(holders))


def _stop_cli(d: Path, job: str) -> int:
    stopped, notes = [], []
    for n in active(d):
        if not n.startswith(job + "."):
            continue
        unreadable = _read(d / f"{n}.proc") is None
        ok = stop(d, n, STOPPED_BY_MARCO)
        if unreadable and stop_requested(d, n):           # who it is cannot be known: it may still be running
            _receipt(d, n, -15, STOPPED_BY_MARCO, survivors=True)
            notes.append(f"ojo: {n}: su .proc no se puede leer, así que no se mandó ninguna señal y su proceso "
                         "puede seguir vivo; revisalo a mano. El job queda bloqueado.")
            continue
        left = (_read(d / f"{n}.receipt.json") or {}).get("survivors")
        if ok:
            stopped.append(n)
            if left:
                notes.append(f"ojo: {n}: se mandó SIGTERM y SIGKILL, pero quedan procesos del grupo vivos; "
                             "revisalos a mano.")
        elif left:
            notes.append(f"ojo: {n}: el proceso principal ya no estaba, pero quedan procesos de su grupo "
                         "que no se pudieron verificar; no se mandó ninguna señal. El job queda bloqueado.")
        elif not (d / f"{n}.receipt.json").exists():
            notes.append(f"{n}: no se pudo verificar qué proceso es (ps no respondió); no se mandó ninguna señal. "
                         "El pedido quedó anotado: cuando termine, el job queda bloqueado. "
                         "Reintentá en unos segundos para detenerlo ya.")
        elif stop_requested(d, n):                        # it had already ended: the job still ends here
            stopped.append(n)
    if stopped:
        print("detenido: " + ", ".join(stopped))
    elif not notes:
        print(f"nada corriendo para {job}")
    for note in notes:
        print(note)
    return 0 if stopped else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["run"] and "--" in argv:
        sep = argv.index("--")
        head, cmd = argv[1:sep], argv[sep + 1:]
        run_dir, name, cwd, pre = Path(head[0]), head[1], ".", []
        for flag, value in zip(head[2::2], head[3::2]):
            if flag == "--cwd":
                cwd = value
            elif flag == "--pre":
                pre.append(value)
        return _wrapper(run_dir, name, cwd, pre, cmd)
    if argv[:1] == ["stop"] and len(argv) == 3 and "/" in argv[1] and not argv[1].startswith("/") \
            and ".." not in argv[1].split("/"):
        repo, _, slug = argv[1].rpartition("/")
        d = paths.run_dir(repo, slug)
        return _stop_cli(d, argv[2])
    print("uso: ark-dispatch stop <repo>/<slug> <job>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
