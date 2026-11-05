"""Detached processes of a run: workers, reviewers, gates, integration checks, ticks.

Each process `name` in a run folder has:
  <name>.proc          pid, command, start, deadline, meta (written at start)
  <name>.log           its stdout and stderr
  <name>.receipt.json  written when it ends: exit, reason, result, usd, session
ark-loop consumes receipts (they move to done/ with the log). A process that died without
a receipt, or ran past its deadline, gets one from reap(). `ark-dispatch stop` stops one by hand.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import paths

# Popen objects of the processes this ark-loop started: poll() them so none stays a zombie.
_children: dict[str, subprocess.Popen] = {}


def _atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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


def _receipt(run_dir: Path, name: str, code: int, reason: str) -> None:
    log = run_dir / f"{name}.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    _atomic(run_dir / f"{name}.receipt.json",
            {"name": name, "exit": code, "reason": reason, "end": time.time(), **parse_output(text)})


def _wrapper(run_dir: Path, name: str, cwd: str, pre: list[str], cmd: list[str]) -> int:
    # PYTHONPATH was set only so this wrapper could import ark; the command gets the original.
    orig = os.environ.pop("ARK_ORIG_PYTHONPATH", None)
    os.environ.pop("PYTHONPATH", None)
    if orig:
        os.environ["PYTHONPATH"] = orig
    code, reason = 0, ""
    with open(run_dir / f"{name}.log", "wb") as log:
        for p in pre:
            code = subprocess.call(["sh", "-c", p], cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
            if code:
                reason = f"setup failed: {p}"
                break
        if not code:
            try:
                code = subprocess.call(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            except OSError as e:
                code, reason = 127, f"cannot start: {e}"
    _receipt(run_dir, name, code, reason)
    return 0


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
    p = subprocess.Popen(argv, env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _children[str(run_dir / name)] = p
    now = time.time()
    info = {"name": name, "pid": p.pid, "cmd": " ".join(cmd)[:300], "cwd": str(cwd), "start": now,
            "deadline": now + minutes * 60, "meta": meta or {}}
    _atomic(run_dir / f"{name}.proc", info)
    return info


def _alive(run_dir: Path, name: str, pid: int) -> bool:
    child = _children.get(str(run_dir / name))
    if child is not None:
        return child.poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


def reap(run_dir) -> list[str]:
    """Give a receipt to every process that died without one or ran past its deadline."""
    run_dir = Path(run_dir)
    done = []
    for n in active(run_dir):
        receipt = run_dir / f"{n}.receipt.json"
        info = _read(run_dir / f"{n}.proc")
        if info is None or receipt.exists():
            continue
        if not _alive(run_dir, n, info["pid"]):
            if not receipt.exists():          # it may have written one while we looked
                _receipt(run_dir, n, -1, "died without a receipt")
            done.append(n)
        elif time.time() > info["deadline"]:
            stop(run_dir, n, "timeout: over its time limit")
            done.append(n)
    return done


def stop(run_dir, name: str, reason: str) -> bool:
    run_dir = Path(run_dir)
    info = _read(run_dir / f"{name}.proc")
    if info is None or (run_dir / f"{name}.receipt.json").exists():
        return False
    try:
        os.killpg(info["pid"], signal.SIGTERM)
    except OSError:
        pass
    for _ in range(50):
        if not _alive(run_dir, name, info["pid"]):
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(info["pid"], signal.SIGKILL)
        except OSError:
            pass
    _receipt(run_dir, name, -15, reason)
    return True


def consume(run_dir, name: str) -> dict:
    """Read a receipt and archive it (with its log) in done/; the process is forgotten."""
    run_dir = Path(run_dir)
    r = _read(run_dir / f"{name}.receipt.json") or {"name": name, "exit": -1, "reason": "unreadable receipt",
                                                    "result": "", "usd": None, "session": None}
    info = _read(run_dir / f"{name}.proc") or {}
    r["meta"] = info.get("meta", {})
    done = run_dir / "done"
    done.mkdir(exist_ok=True)
    stamp = f"{name}-{time.time_ns()}"
    _atomic(done / f"{stamp}.json", {**r, "proc": info})
    log = run_dir / f"{name}.log"
    if log.exists():
        log.replace(done / f"{stamp}.log")
    for suffix in (".receipt.json", ".proc"):
        (run_dir / f"{name}{suffix}").unlink(missing_ok=True)
    _children.pop(str(run_dir / name), None)
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
    if argv[:1] == ["stop"] and len(argv) == 3 and "/" in argv[1]:
        repo, _, slug = argv[1].rpartition("/")
        d = paths.run_dir(repo, slug)
        stopped = [n for n in active(d) if n.startswith(argv[2] + ".") and stop(d, n, "stopped by Marco")]
        print("detenido: " + ", ".join(stopped) if stopped else f"nada corriendo para {argv[2]}")
        return 0 if stopped else 1
    print("uso: ark-dispatch stop <repo>/<slug> <job>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
