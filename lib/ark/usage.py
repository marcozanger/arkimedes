"""What a worker's stream-json log says so far: tokens spent and its last message. Only reads.

A worker can write in the run folder, so nothing here trusts the log: a link or a FIFO reads as nothing, a
malformed line is skipped, and text passes through clean() before a terminal or a prompt sees it."""
from __future__ import annotations

import json
import os
import re
import stat

LAST_MESSAGE = 500        # characters of the last message kept
CHUNK = 1 << 20           # bytes read at a time
MAX_LINE = 1 << 22        # bytes of an unfinished line kept; a longer one fails to parse and is skipped
TOOL_INPUT = 200          # characters of a tool's input shown by render_line
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_COUNTED = ("input_tokens", "cache_creation_input_tokens", "output_tokens")

_seen: dict[str, dict] = {}  # per path, so a log is never read twice from the start


def clean(text: str) -> str:
    """Log text is untrusted: no terminal escapes, no NUL, no lone surrogate. Keeps newlines and tabs."""
    return _CONTROL.sub("", text).encode("utf-8", "replace").decode("utf-8")


def _open(path) -> int | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return fd


def read_from(path, offset: int = 0) -> tuple[bytes, int]:
    """The bytes from offset on, and the new offset. Nothing, for anything but a regular file."""
    fd = _open(path)
    if fd is None:
        return b"", offset
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        out = bytearray()
        while chunk := os.read(fd, CHUNK):
            out += chunk
        return bytes(out), offset + len(out)
    finally:
        os.close(fd)


def _parse(raw) -> dict | None:
    try:
        d = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return d if isinstance(d, dict) else None


def _message(d: dict) -> dict:
    m = d.get("message")
    return m if isinstance(m, dict) else {}


def _blocks(d: dict) -> list[dict]:
    content = _message(d).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tokens(d: dict) -> int:
    u = _message(d).get("usage")
    if not isinstance(u, dict):
        return 0
    values = [u.get(k) for k in _COUNTED]
    return sum(v for v in values if type(v) is int and v > 0)


def _take(s: dict, raw: bytes) -> None:
    d = _parse(raw)
    if d is None:
        return
    if d.get("type") == "result" and isinstance(d.get("result"), str) and d["result"].strip():
        s["last"] = clean(d["result"]).strip()[-LAST_MESSAGE:]
    if d.get("type") != "assistant":
        return
    n, mid = _tokens(d), _message(d).get("id")
    if isinstance(mid, str):  # a message arrives once per content block, each with its usage so far
        s["usage"][mid] = max(s["usage"].get(mid, 0), n)
    else:
        s["loose"] += n
    texts = [b["text"] for b in _blocks(d) if b.get("type") == "text" and isinstance(b.get("text"), str)]
    if any(t.strip() for t in texts):
        s["last"] = clean("\n".join(texts)).strip()[-LAST_MESSAGE:]


def scan(path) -> dict:
    """{"tokens": n, "last": text} of everything in the log so far. Reads only what is new since last time."""
    key = str(path)
    fd = _open(path)
    if fd is None:
        _seen.pop(key, None)
        return {"tokens": 0, "last": ""}
    try:
        info = os.fstat(fd)
        s = _seen.get(key)
        if s is None or s["inode"] != info.st_ino or info.st_size < s["offset"]:
            s = _seen[key] = {"inode": info.st_ino, "offset": 0, "tail": b"", "usage": {}, "loose": 0, "last": ""}
        os.lseek(fd, s["offset"], os.SEEK_SET)
        while chunk := os.read(fd, CHUNK):
            s["offset"] += len(chunk)
            lines = (s["tail"] + chunk).split(b"\n")
            s["tail"] = lines.pop()[-MAX_LINE:]
            for raw in lines:
                _take(s, raw)
    finally:
        os.close(fd)
    return {"tokens": sum(s["usage"].values()) + s["loose"], "last": s["last"]}


def render_line(line) -> str | None:
    """One log line for a person, or None when it says nothing worth showing."""
    d = _parse(line)
    if d is None:
        text = line.decode("utf-8", "replace") if isinstance(line, bytes) else line
        return clean(text).strip() or None
    kind = d.get("type")
    if kind == "system":
        return f"── {clean(str(d.get('subtype', 'system')))}"
    if kind == "result":
        cost = d.get("total_cost_usd")
        money = f"${cost:.2f}" if type(cost) in (int, float) else "$?"
        return f"── {'ended with an error' if d.get('is_error') else 'ended'} · {money}"
    if kind != "assistant":
        return None
    out = []
    for b in _blocks(d):
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            out.append(clean(b["text"]).strip())
        elif b.get("type") == "tool_use":
            out.append(clean(f"→ {b.get('name')} {json.dumps(b.get('input'), ensure_ascii=False)}")[:TOOL_INPUT])
    return "\n".join(t for t in out if t) or None
