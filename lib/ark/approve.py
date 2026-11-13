"""Approving a plan: the one implementation behind `ark approve` and `ark ui`. Only Marco approves."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import paths, validate
from .plan import load_plan, set_status, sha256_file


class ApproveError(Exception):
    def __init__(self, kind: str, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.kind, self.problems = kind, problems or []   # kind: role · read · invalid · changed


def check(path: Path) -> list[str]:
    """The contract problems that block approval; empty when it can be approved."""
    return validate.validate(path)


def approve_plan(path: Path, expect_sha: str | None = None) -> dict:
    if "ARK_ROLE" in os.environ:
        raise ApproveError("role", "refused: a runner process (ARK_ROLE) cannot approve; only Marco approves plans.")
    path = Path(path)
    try:
        sha_now = sha256_file(path)
    except OSError as e:
        raise ApproveError("read", f"could not read {path}: {e}")
    if expect_sha is not None and sha_now != expect_sha:
        raise ApproveError("changed", "the plan changed since it was shown; look at it again.")
    problems = check(path)
    if problems:
        raise ApproveError("invalid", "the plan does not meet the contract.", problems)
    plan = load_plan(path)
    if sha256_file(path) != sha_now:
        raise ApproveError("changed", "the plan changed while it was being approved.")
    set_status(path, "closed")
    sha = sha256_file(path)
    repo, slug = str(plan.frontmatter["repo"]), str(plan.frontmatter["slug"])
    f = paths.approval_file(repo, slug)
    f.parent.mkdir(parents=True, exist_ok=True)
    rec = {"repo": repo, "slug": slug, "plan": str(path.resolve()), "sha256": sha,
           "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)
    return rec
