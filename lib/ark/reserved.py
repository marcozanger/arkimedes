"""The reserved list: what only Marco decides, checked on a change without a model.

Euklides may do anything a change does not hit here (spec: desk/arkimedes/euklides-blast-radius). The
mechanical part, on a branch's diff:

- R2 weakening verification: a test file deleted, fewer test functions or asserts in a file than before,
  a new skip / xfail marker;
- R3 another live job's area: this run's other jobs, and the live jobs of every other run of the repo;
- R5 public contracts by path: lockfiles, dependency manifests, migrations (`euklides.reserved`).

R1 (what is delivered) and the rest of R5 are Euklides' own call (`undo: plan | contract`), R4 is the
budget's (loop._raise), and R6 is the push layer's and merge-guard's. A check that cannot run is a hit:
an unknown must not license an action."""
from __future__ import annotations

import re
from pathlib import Path

from . import companions, paths, run
from .globs import match_any

DEFAULT_GLOBS = [
    "**/package-lock.json", "**/yarn.lock", "**/pnpm-lock.yaml", "**/poetry.lock", "**/uv.lock", "**/Cargo.lock",
    "**/go.sum", "**/Gemfile.lock", "**/pubspec.lock", "**/composer.lock",
    "**/package.json", "**/requirements*.txt", "**/pyproject.toml", "**/Cargo.toml", "**/go.mod",
    "**/pubspec.yaml", "**/Gemfile",
    "**/migrations/**", "**/migrate/**",
]
REVIEW_FILES, REVIEW_LINES = 3, 30  # a change over either goes through a reviewer before it lands; it is not asked

_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+test_\w*|^\s*(?:it|test)\s*\(|^\s*func\s+Test\w*\(")
_ASSERT = re.compile(r"^\s*assert\b|\bassert\w*\s*\(|\bexpect\s*\(|\bself\.assert\w+\(")
_SKIP = re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail)\s*\(|@unittest\.skip|"
                   r"\b(?:it|test|describe)\.(?:skip|todo)\s*\(|\bx(?:it|describe|test)\s*\(|\bt\.Skip\w*\(")


def _hit(rule: str, file: str, why: str) -> dict:
    return {"rule": rule, "file": file, "why": why}


def _files_of(diff: str) -> dict[str, dict]:
    """Per file of a unified diff (`git diff -U0` or more): added and removed lines, and whether it was deleted."""
    out: dict[str, dict] = {}
    cur = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            name = m.group(2) if m else line
            cur = out.setdefault(name, {"add": [], "del": [], "deleted": False})
        elif cur is None:
            continue
        elif line.startswith("deleted file mode"):
            cur["deleted"] = True
        elif line.startswith("+++ ") or line.startswith("--- "):
            continue
        elif line.startswith("+"):
            cur["add"].append(line[1:])
        elif line.startswith("-"):
            cur["del"].append(line[1:])
    return out


def weakened(diff: str) -> list[dict]:
    """R2 on a unified diff. Counts, per test file, what left against what came: rewriting an assert or
    moving a test is fine, losing one is not. A new skip or xfail marker anywhere is a hit."""
    hits = []
    for f, d in _files_of(diff).items():
        added = [x for x in d["add"] if _SKIP.search(x)]
        removed = [x for x in d["del"] if _SKIP.search(x)]
        if len(added) > len(removed):
            hits.append(_hit("R2", f, f"a new skip or xfail: {added[0].strip()[:120]}"))
        if not companions.is_companion(f) or f.endswith(".md"):
            continue
        if d["deleted"]:
            hits.append(_hit("R2", f, "a test file is deleted"))
            continue
        for what, rx in (("test functions", _TEST_DEF), ("asserts", _ASSERT)):
            gone = sum(1 for x in d["del"] if rx.search(x)) - sum(1 for x in d["add"] if rx.search(x))
            if gone > 0:
                hits.append(_hit("R2", f, f"{gone} fewer {what} than before"))
    return hits


def foreign(files: list[str], areas: dict[str, list[str]]) -> list[dict]:
    """R3: files inside another live job's area. `areas` maps a job label to its globs."""
    hits = []
    for f in files:
        owners = [who for who, globs in areas.items() if match_any(f, globs)]
        if owners:
            hits.append(_hit("R3", f, f"in the area of {', '.join(sorted(owners))}"))
    return hits


def contracts(files: list[str], globs: list[str]) -> list[dict]:
    """R5 by path."""
    return [_hit("R5", f, "a public contract (dependencies, lockfile or migration)") for f in files
            if match_any(f, globs)]


def check(files: list[str], diff: str | None, areas: dict[str, list[str]] | None, globs: list[str]) -> list[dict]:
    """Every R hit by a change. `diff` or `areas` None means they could not be read: a hit of its own."""
    hits = []
    if diff is None:
        hits.append(_hit("R2", "", "the diff could not be read, so a weaker check cannot be ruled out"))
    else:
        hits += weakened(diff)
    if areas is None:
        hits.append(_hit("R3", "", "the other jobs' areas could not be read"))
    else:
        hits += foreign(files, areas)
    return hits + contracts(files, globs)


def text(hits: list[dict]) -> str:
    """The hits in words Marco reads, one per line."""
    return "\n".join(f"{h['rule']}: {h['file'] + ' — ' if h['file'] else ''}{h['why']}" for h in hits)


def other_runs_areas(repo: str, slug: str) -> dict[str, list[str]] | None:
    """The live jobs' areas of every other run of the repo, as `<slug>/<job>`; None when a state is unreadable.
    A run that is finished, or a job that merged or is blocked, owns nothing any more."""
    out: dict[str, list[str]] = {}
    folder = paths.run_dir(repo, slug).parent
    if not folder.is_dir():
        return out
    for state in sorted(folder.glob("*/state.json")):
        other = state.parent.name
        if other == slug:
            continue
        try:
            st = run.load(repo, other)
        except run.StateCorrupt:
            return None
        if not st or st.get("status") in run.FINISHED or st.get("closing") == "done":
            continue
        areas = _plan_areas(st)
        if areas is None:
            return None
        for job, j in (st.get("jobs") or {}).items():
            if j.get("status") in run.TERMINAL or job not in areas:
                continue
            out[f"{other}/{job}"] = [*areas[job], *(st.get("grants") or {}).get(job, [])]
    return out


def _plan_areas(st: dict) -> dict[str, list[str]] | None:
    from .plan import load_plan  # late: plan imports a lot
    try:
        p = load_plan(Path(st["plan"]))
        return {m["id"]: list(m.get("area") or []) for m in p.manifest["jobs"]}
    except Exception:
        return None
