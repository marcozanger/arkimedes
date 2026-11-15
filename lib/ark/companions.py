"""Companion files: the tests, test helpers and docs a job's change usually drags along.

One pure question: is this repo-relative path a companion? A companion outside a job's area is allowed when
the change is small and no other job owns it (the runner checks both; see loop._absorb_companions). A source
file (lib/, src/, config) is never a companion."""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

TEST_DIRS = ("tests", "test")  # at the repo root
BASENAMES = ("conftest.py",)
BASENAME_GLOBS = ("*.test.*", "*.spec.*")
NESTED_DIR = "__tests__"


def is_companion(path: str) -> bool:
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return False
    parts = PurePosixPath(path).parts
    if not parts or any(p in ("..", ".", ".git") for p in parts):
        return False
    name = parts[-1]
    if len(parts) > 1 and parts[0] in TEST_DIRS:
        return True
    if NESTED_DIR in parts[:-1]:
        return True
    if name in BASENAMES or any(fnmatchcase(name, g) for g in BASENAME_GLOBS):
        return True
    if parts[:2] == ("docs", "decisions") and len(parts) > 2:
        return True
    return len(parts) == 2 and parts[0] == "docs" and name.endswith(".md") and len(name) > 3
