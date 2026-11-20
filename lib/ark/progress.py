"""Is a job getting anywhere? A failure's signature, and a check over a job's last failures.

The signature is what failed, not how much: the red test ids a gate printed (pytest `FAILED a::b`,
jest/vitest `FAIL path` and `✕ name`) plus the first error line, with numbers, hex and paths under
temp folders blanked so that a timing or an address does not make two equal failures look different.
A job is stalled when its last two failures have the same signature on the same head: nothing changed
between them, so more money buys the same failure again (the runner then refuses `raise`)."""
from __future__ import annotations

import hashlib
import re

KEEP = 6  # failures kept per job

_IDS = [
    re.compile(r"^(?:FAILED|ERROR)\s+(\S+::\S+)", re.M),  # pytest -q / -rf summary
    re.compile(r"^\s*FAIL\s+(\S+)", re.M),                 # jest, vitest: a red file
    re.compile(r"^\s*[✕×]\s+(.+?)\s*(?:\(\d+\s*m?s\))?$", re.M),  # jest, vitest: a red test
    re.compile(r"^--- FAIL: (\S+)", re.M),                 # go test
]
_ERROR = re.compile(r"(?i)\b(error|exception|assert|failed|panic|traceback)\b")
_NOISE = [
    (re.compile(r"/(?:private/)?(?:var/folders|tmp)/\S*"), "<tmp>"),
    (re.compile(r"\b(?:0x[0-9a-fA-F]+|[0-9a-f]{7,40})\b"), "<hex>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
]


def red_ids(text: str) -> list[str]:
    """The failing tests a gate's output names, sorted, without duplicates."""
    out = set()
    for rx in _IDS:
        out.update(m.group(1).strip() for m in rx.finditer(text or ""))
    return sorted(out)


def _plain(line: str) -> str:
    for rx, to in _NOISE:
        line = rx.sub(to, line)
    return " ".join(line.split())[:300]


def first_error(text: str) -> str:
    """The first line that reads like an error, with its noise blanked; else the first non-empty line."""
    lines = [x for x in (text or "").splitlines() if x.strip()]
    for x in lines:
        if _ERROR.search(x):
            return _plain(x)
    return _plain(lines[0]) if lines else ""


def signature(text: str) -> str:
    ids = red_ids(text)
    key = "\n".join(ids) + "\n--\n" + first_error(text)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def note(j: dict, text: str, head: str | None) -> dict:
    """Record a failure on the job (its last KEEP), and return the entry."""
    e = {"sig": signature(text), "head": head, "ids": red_ids(text)[:20], "error": first_error(text)}
    j["failures"] = (j.get("failures") or [])[-(KEEP - 1):] + [e]
    return e


def stalled(j: dict) -> bool:
    """The last two failures are the same failure on the same head: no progress between them."""
    f = j.get("failures") or []
    return len(f) >= 2 and f[-1]["sig"] == f[-2]["sig"] and f[-1]["head"] == f[-2]["head"]


def why_stalled(j: dict) -> str:
    f = (j.get("failures") or [])[-1]
    what = ", ".join(f["ids"][:5]) or f["error"] or "the same error"
    return f"the last two attempts failed the same way ({what}) on the same head {str(f['head'])[:8]}"
