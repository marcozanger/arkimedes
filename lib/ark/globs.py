"""Do two area globs possibly touch the same file? Conservative: when unsure, yes.

Each glob is cut at its first segment with a wildcard; the literal segments before it are its
prefix. Two globs overlap when one prefix is a segment-prefix of the other. A fully literal path
is its own prefix, so two different files never overlap.
"""
from __future__ import annotations

WILDCARDS = set("*?[{")


def _prefix(glob: str) -> list[str]:
    out: list[str] = []
    for part in glob.strip().split("/"):
        if part in ("", "."):
            continue
        if any(c in WILDCARDS for c in part):
            break
        out.append(part)
    return out


def overlap(a: str, b: str) -> bool:
    pa, pb = _prefix(a), _prefix(b)
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


def areas_overlap(a: list[str], b: list[str]) -> bool:
    return any(overlap(x, y) for x in a for y in b)
