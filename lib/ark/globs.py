"""Do two area globs possibly touch the same file? Conservative: when unsure, yes.

Each glob is cut at its first segment with a wildcard; the literal segments before it are its
prefix. Two globs overlap when one prefix is a segment-prefix of the other. A fully literal path
is its own prefix, so two different files never overlap.
"""
from __future__ import annotations

import re

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


def _regex(glob: str) -> re.Pattern:
    g = glob.strip().removeprefix("./")
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif g.startswith("**", i):
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif g[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return re.compile(out + r"\Z")


def match(path: str, glob: str) -> bool:
    """Does a repo-relative file path match an area glob? `**` crosses folders, `*` does not.
    A glob without wildcards also matches every file under it (a folder)."""
    path = path.removeprefix("./")
    if not any(c in WILDCARDS for c in glob):
        folder = glob.strip().removeprefix("./").rstrip("/")
        if path == folder or path.startswith(folder + "/"):
            return True
    return bool(_regex(glob).match(path))


def match_any(path: str, globs: list[str]) -> bool:
    return any(match(path, g) for g in globs)
