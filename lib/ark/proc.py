"""Detached processes of a run: workers, reviewers, gates, integration checks, ticks.

Each process `name` in a run folder has:
  <name>.proc          pid, wrapper identity, command, start, deadline, meta (written at start)
  <name>.log           its stdout and stderr
  <name>.receipt.json  written when it ends: exit, reason, result, usd, session
ark-loop consumes receipts (they move to done/ with the log). A process that died without
a receipt, or ran past its deadline, gets one from reap(). `ark-dispatch stop` stops one by hand.

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

OURS, GONE, REUSED, UNKNOWN = "ours", "gone", "reused", "unknown"

# ps must answer the same whatever the caller's locale, timezone or PATH.
PS_ENV = {"LC_ALL": "C", "TZ": "UTC", "PATH": "/bin:/usr/bin"}

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
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or (path.suffix == ".proc" and not _valid_proc(d)):
        return None
    return d


def parse_output(text: str) -> dict:
    """The last `{"type": "result"}` line of claude's json / stream-json output. For any other
    program, the tail of its output."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "result":
            return {"result": str(d.get("result") or ""), "usd": d.get("total_cost_usd"),
                    "session": d.get("session_id"), "is_error": bool(d.get("is_error"))}
    return {"result": text[-4000:], "usd": None, "session": None, "is_error": None}


def _receipt(run_dir: Path, name: str, code: int, reason: str, **extra) -> None:
    """Write the receipt unless there is one: the first word on how a process ended stands."""
    path = run_dir / f"{name}.receipt.json"
    if path.exists():
        return
    log = run_dir / f"{name}.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    _atomic(path, {"name": name, "exit": code, "reason": reason, "end": time.time(), **parse_output(text), **extra})


def _ps(*args: str) -> str | None:
    """ps's output, or None if it could not answer. No output means no such process."""
    try:
        r = subprocess.run(["ps", *args], capture_output=True, text=True, errors="replace", timeout=5, env=PS_ENV,
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
    with open(run_dir / f"{name}.log", "wb") as log:
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
    for a process that survives SIGTERM. A wrapper that is already gone gets its death recorded."""
    run_dir = Path(run_dir)
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
    r["meta"] = info.get("meta", {})
    done = run_dir / "done"
    done.mkdir(exist_ok=True)
    end = r["end"]
    stamp = f"{name}-{int(end * 1e9) if isinstance(end, (int, float)) else time.time_ns()}"
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
        stopped = [n for n in active(d) if n.startswith(argv[2] + ".") and stop(d, n, "stopped by Marco")]
        print("detenido: " + ", ".join(stopped) if stopped else f"nada corriendo para {argv[2]}")
        if any((_read(d / f"{n}.receipt.json") or {}).get("survivors") for n in stopped):
            print("ojo: quedan procesos del grupo que no se pudieron verificar; no se les mandó ninguna señal.")
        return 0 if stopped else 1
    print("uso: ark-dispatch stop <repo>/<slug> <job>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
