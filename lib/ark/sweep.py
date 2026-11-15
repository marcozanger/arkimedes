"""The sweep: what finished and abandoned runs leave behind (worktrees, ark/* branches here and on origin, their
open PRs, empty folders), what of it must stay, and clearing the rest.

A run is sweepable when it is aborted, done with every job merged, or abandoned: closed blocked (or done with a
job not merged) for `sweep.abandon_days` without a retry. Anything a live run or a run still inside that window
could need is never touched, and neither is anything with work that exists nowhere else."""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import gitops, history, paths, run

DEFAULTS = {"enabled": True, "abandon_days": 14}


def settings() -> dict:
    """The workspace's `sweep` block over DEFAULTS; a bad value or an unknown key is a ValueError naming it."""
    block = paths.load_workspace().get("sweep")
    if block is None:
        block = {}
    if not isinstance(block, dict):
        raise ValueError("sweep must be an object")
    for k in block:
        if k not in DEFAULTS:
            raise ValueError(f"sweep.{k} is not a setting (known: {', '.join(DEFAULTS)})")
    out = {**DEFAULTS, **block}
    if not isinstance(out["enabled"], bool):
        raise ValueError("sweep.enabled must be true or false")
    days = out["abandon_days"]
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("sweep.abandon_days must be a whole number of days, at least 1")
    return out


def _when(text) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        t = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def closed_at(st: dict) -> datetime | None:
    """When the run closed: `closed_at` when the state has it, else the time of its last event."""
    if st.get("closed_at"):
        return _when(st["closed_at"])
    events = st.get("events") or []
    if events and isinstance(events[-1], str) and " · " in events[-1]:
        return _when(events[-1].split(" · ", 1)[0])
    return None


def classify(st: dict, now: datetime) -> tuple[str, datetime | None]:
    """(kind, since): live, aborted, done, abandoned (since the day it became so) or window (until that day).
    A closed run with no close time at all stays in the window for good: there is no date to count from."""
    status = st.get("status")
    if status in ("running", "waiting") or st.get("closing") != "done":
        return "live", None
    closed = closed_at(st)
    if status == "aborted":
        return "aborted", closed
    jobs = st.get("jobs") or {}
    if status == "done" and all(j.get("status") == "merged" for j in jobs.values()):
        return "done", closed
    if closed is None:
        return "window", None
    until = closed + timedelta(days=settings()["abandon_days"])
    return ("abandoned" if now >= until else "window"), until


def abandoned(st: dict, now: datetime) -> datetime | None:
    kind, since = classify(st, now)
    return since if kind == "abandoned" else None


# --- the plan: what would go, what is held, and why

SCRATCH_ROOTS = ("_ticks", "_forge")   # the runner's own scratch under ARK_WORKTREES, never a run's
SWEEPABLE = ("aborted", "done", "abandoned")


@dataclass
class Item:
    kind: str        # "worktree" | "local_branch" | "remote_branch" | "pr" | "folder"
    target: str      # path, branch name, or PR url
    repo: str        # repo name under a workspace root ("" for a bare folder)
    slug: str
    size: int = 0    # bytes, worktrees only
    number: int | None = None  # PRs only
    branch: str = ""  # worktrees and PRs: the branch that goes with them (skipped when they fail)

    def key(self) -> str:
        return f"{self.kind}:{self.repo}/{self.slug}:{self.target}"


@dataclass
class RunPlan:
    repo: str
    slug: str
    why: str         # "aborted" | "done" | "abandoned since 2026-11-01" | "orphan"
    items: list[Item] = field(default_factory=list)
    held: list[tuple[Item, str]] = field(default_factory=list)   # (item, reason)


def _states(problems: list[str]) -> tuple[list[dict], set[str]]:
    """Every readable run state, and the `<repo>/<slug>` of every state file, readable or not."""
    out, seen, root = [], set(), paths.runs_dir()
    for f in sorted(root.glob("**/state.json")):
        rel = f.parent.relative_to(root)
        seen.add(rel.as_posix())
        try:
            st = run.load(str(rel.parent), rel.name)
        except run.StateCorrupt as e:
            problems.append(f"{rel.as_posix()}: skipped, {e}")
            continue
        if st and isinstance(st.get("repo"), str) and isinstance(st.get("slug"), str):
            out.append(st)
        elif st:
            problems.append(f"{rel.as_posix()}: skipped, the state names no repo or slug")
    return out, seen


def _names(st: dict) -> list[str]:
    """The run's job and fix branches, from its job list (never by prefix: another run's names can share it)."""
    slug = st["slug"]
    names = [f"ark/{slug}-{job}" for job in st.get("jobs") or {}]
    names += [f"ark/{slug}-_fix-{f['n']}" for f in st.get("fixes") or [] if isinstance(f, dict) and "n" in f]
    return names


def _size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _repo_of(path: Path) -> Path | None:
    """The main checkout a worktree belongs to, from the gitdir in its .git file."""
    try:
        line = (Path(path) / ".git").read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line.split(":", 1)[1].strip())
    gitdir = gitdir if gitdir.is_absolute() else Path(path) / gitdir
    if gitdir.parent.name != "worktrees" or gitdir.parent.parent.name != ".git":
        return None
    return gitdir.parent.parent.parent


def _dirty(wt: Path) -> str | None:
    """Why this worktree must stay, or None. A folder with no .git is a leftover with nothing of git in it."""
    if not os.path.lexists(wt / ".git") or wt.is_symlink():
        return None
    try:
        return None if gitops.is_clean(wt) else "uncommitted changes"
    except gitops.GitError as e:
        return f"cannot read its status: {e}"


def _local(repo: Path, b: str) -> str | None:
    return gitops.git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}^{{commit}}", check=False) or None


def _unpushed(repo: Path, b: str, tip: str, head: str | None) -> int:
    """Commits of the local branch that origin does not have, unless its tip is the head the run recorded."""
    if head and tip == head:
        return 0
    if gitops.git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{b}", check=False):
        out = gitops.git(repo, "rev-list", "--count", tip, "--not", f"refs/remotes/origin/{b}")
    else:
        out = gitops.git(repo, "rev-list", "--count", tip, "--not", "--remotes=origin")
    return int(out or 0)


def _run_plan(st: dict, why: str, repo: Path, forge, claimed: set[str], others: list[Path],
              in_use: dict[str, Path]) -> RunPlan:
    name, slug = st["repo"], st["slug"]
    rp = RunPlan(name, slug, why)
    folder = paths.worktrees_dir() / name / slug
    held_branch: dict[str, str] = {}
    mine: set[Path] = set()
    if folder.is_dir() and not folder.is_symlink():
        for wt in sorted(folder.iterdir()):
            if not (wt.is_dir() or wt.is_symlink()) or any(o == wt or wt in o.parents for o in others):
                continue
            item = Item("worktree", str(wt), name, slug, size=0 if wt.is_symlink() else _size(wt),
                        branch=f"ark/{slug}-{wt.name}")
            reason = _dirty(wt)
            if reason:
                rp.held.append((item, reason))
                held_branch[item.branch] = reason
            else:
                rp.items.append(item)
                mine.add(wt.resolve())
    heads = {f"ark/{slug}-{j}": (job.get("head"), job.get("status") == "merged")
             for j, job in (st.get("jobs") or {}).items() if isinstance(job, dict)}
    heads.update({f"ark/{slug}-_fix-{f['n']}": (f.get("head"), f.get("status") == "landed")
                  for f in st.get("fixes") or [] if isinstance(f, dict) and "n" in f})
    names = _names(st)
    pr = st.get("final_pr")
    base_merged = False
    if pr:
        try:
            state = forge.view(repo, pr["number"])["state"]
        except Exception as e:   # a forge or a state we cannot read: ark/<slug> stays, the rest goes on
            rp.held.append((Item("local_branch", f"ark/{slug}", name, slug), f"final PR unreadable: {e}"))
            state = None
        if state in ("MERGED", "CLOSED"):
            names.append(f"ark/{slug}")
            base_merged = state == "MERGED"
    else:
        names.append(f"ark/{slug}")
    heads.setdefault(f"ark/{slug}", (None, base_merged))
    for b in names:
        if b in claimed:
            continue
        tip, on_origin = _local(repo, b), gitops.remote_sha(repo, b) is not None
        found = ([Item("local_branch", b, name, slug)] if tip else []) + \
                ([Item("remote_branch", b, name, slug)] if on_origin else [])
        if not found:
            continue
        head, merged = heads.get(b, (None, False))
        reason = held_branch.get(b)
        where = in_use.get(b) if tip else None
        if not reason and where is not None and where.resolve() not in mine:
            reason = f"in use at {where}"
        if not reason and tip and not merged:
            n = _unpushed(repo, b, tip, head)
            reason = f"{n} commits not on origin" if n else None
        if reason:
            rp.held += [(i, reason) for i in found]
            continue
        rp.items += found
        if on_origin:
            for p in forge.open_by_head(repo, b):
                rp.items.append(Item("pr", p["url"], name, slug, number=p["number"], branch=b))
    return rp


def _why(kind: str, since: datetime | None) -> str:
    return f"abandoned since {since.date().isoformat()}" if kind == "abandoned" else kind


def _orphans(root: Path, known: set[str], problems: list[str]) -> list[RunPlan]:
    """Worktrees under ARK_WORKTREES whose <repo>/<slug> has no state file at all."""
    found: dict[str, RunPlan] = {}

    def walk(d: Path, rel: tuple[str, ...]) -> None:
        for child in sorted(d.iterdir()):
            if child.is_symlink() or not child.is_dir() or (not rel and child.name in SCRATCH_ROOTS):
                continue
            if not os.path.lexists(child / ".git"):
                if "/".join((*rel, child.name)) not in known:
                    walk(child, (*rel, child.name))
                continue
            if len(rel) < 2 or "/".join(rel) in known:
                continue
            if _repo_of(child) is None or not _repo_of(child).is_dir():
                problems.append(f"{child}: an orphan worktree whose repo cannot be found from its .git file")
                continue
            name, slug = "/".join(rel[:-1]), rel[-1]
            rp = found.setdefault("/".join(rel), RunPlan(name, slug, "orphan"))
            item = Item("worktree", str(child), name, slug, size=_size(child))
            reason = _dirty(child)
            if reason:
                rp.held.append((item, reason))
            else:
                rp.items.append(item)

    walk(root, ())
    return list(found.values())


def _folders(root: Path, gone: set[Path], keep: set[str]) -> list[Path]:
    """Folders under ARK_WORKTREES that are empty, or will be once `gone` is: deepest first. Never the root,
    its scratch folders, a worktree, or the folder of a run that is not swept (`keep`)."""
    out: list[Path] = []

    def removable(d: Path, rel: tuple[str, ...]) -> bool:
        ok = True
        for c in sorted(d.iterdir()):
            crel = (*rel, c.name)
            if c.resolve() in gone:
                continue
            if (c.is_dir() and not c.is_symlink() and not os.path.lexists(c / ".git")
                    and not (not rel and c.name in SCRATCH_ROOTS) and "/".join(crel) not in keep
                    and removable(c, crel)):
                out.append(c)
                continue
            ok = False
        return ok

    removable(root, ())
    return out


def plan(now: datetime, forge) -> tuple[list[RunPlan], list[str]]:
    """(plans, problems): what a sweep would remove per run, and what it holds back and why. Nothing is changed
    here except fetching each repo once. A run that cannot be read or reached is a problem, never an orphan."""
    problems: list[str] = []
    states, known = _states(problems)
    kinds = {}
    for st in states:
        try:
            kinds[id(st)] = classify(st, now)
        except Exception as e:  # a state with fields of the wrong shape
            problems.append(f"{st['repo']}/{st['slug']}: skipped, cannot classify it: {e}")
            kinds[id(st)] = ("window", None)
    claimed = {b for st in states if kinds[id(st)][0] not in SWEEPABLE for b in _names(st) + [f"ark/{st['slug']}"]}
    root = paths.worktrees_dir()
    keep = {f"{st['repo']}/{st['slug']}" for st in states if kinds[id(st)][0] not in SWEEPABLE}
    keep |= known - {f"{st['repo']}/{st['slug']}" for st in states}   # unreadable states: hands off
    plans: list[RunPlan] = []
    repos: dict[str, tuple[Path, dict[str, Path]] | None] = {}
    for st in states:
        kind, since = kinds[id(st)]
        if kind not in SWEEPABLE:
            continue
        name = st["repo"]
        if name not in repos:
            repos[name] = None
            path = paths.find_repo(name)
            if path is None:
                problems.append(f"{name}: no such repo under the workspace roots; its runs are skipped")
                continue
            try:
                gitops.fetch(path)
                in_use = {b: p for p, b in gitops.worktrees(path) if b}
            except gitops.GitError as e:
                problems.append(f"{name}: skipped, {e}")
                continue
            repos[name] = (path, in_use)
        if repos[name] is None:
            continue
        path, in_use = repos[name]
        others = [root / s["repo"] / s["slug"] for s in states if s is not st] + \
                 [root / k for k in known]
        others = [o for o in others if o != root / name / st["slug"]]
        try:
            rp = _run_plan(st, _why(kind, since), path, forge, claimed, others, in_use)
        except Exception as e:  # git or the forge failing for this run: the others still go on
            problems.append(f"{name}/{st['slug']}: skipped, {e}")
            continue
        plans.append(rp)
    if root.is_dir():
        plans += _orphans(root, known, problems)
        gone = {Path(i.target).resolve() for rp in plans for i in rp.items if i.kind == "worktree"}
        loose = RunPlan("", "", "orphan")
        for d in _folders(root, gone, keep):
            rel = d.relative_to(root).as_posix()
            owner = next((rp for rp in plans if rp.repo and (rel == f"{rp.repo}/{rp.slug}"
                                                              or rel.startswith(f"{rp.repo}/{rp.slug}/"))), loose)
            owner.items.append(Item("folder", str(d), owner.repo, owner.slug))
        plans.append(loose)
    return [rp for rp in plans if rp.items or rp.held], problems


# --- carrying out a plan, and the log

LOG = "sweeps.jsonl"
COMMENT = "Closed by the Arkimedes sweep: run {repo}/{slug} is {why}."
_BRANCH_ORDER = {"pr": 0, "local_branch": 1, "remote_branch": 2}


def _log(rp: RunPlan, item: Item, now: datetime) -> None:
    """One line in history/sweeps.jsonl, under history/.lock like the other history files."""
    d = history.history_dir()
    d.mkdir(parents=True, exist_ok=True)
    row = {"at": now.isoformat(), "repo": item.repo, "slug": item.slug, "why": rp.why, "kind": item.kind,
           "target": item.target}
    with open(d / ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(d / LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _worktree_repo(item: Item) -> Path:
    repo = _repo_of(Path(item.target))
    if repo is not None and repo.is_dir():
        return repo
    found = paths.find_repo(item.repo) if item.repo else None
    if found is None:
        raise gitops.GitError(f"cannot tell which repo {item.target} belongs to")
    return found


def apply(plans: list[RunPlan], forge, now: datetime) -> dict:
    """Carry out the plans: worktrees, then each branch (its open PRs closed with a comment first, then the
    local branch, then origin's), then empty folders deepest first. Held items are left alone. An item that
    fails is reported and skipped with the rest of its branch; the others go on. Every deletion is logged."""
    removed: list[str] = []
    errors: list[tuple[str, str]] = []
    held = [(i.key(), why) for rp in plans for i, why in rp.held]
    skip: set[tuple[str, str, str]] = set()   # (repo, slug, branch) whose items must stay
    repos: dict[str, Path | None] = {}

    def repo_of(name: str) -> Path:
        if name not in repos:
            repos[name] = paths.find_repo(name)
        if repos[name] is None:
            raise gitops.GitError(f"{name}: no such repo under the workspace roots")
        return repos[name]

    def done(rp: RunPlan, item: Item) -> None:
        _log(rp, item, now)
        removed.append(item.key())

    def failed(item: Item, e: Exception) -> None:
        errors.append((item.key(), str(e)))
        if item.branch:
            skip.add((item.repo, item.slug, item.branch))

    for rp in plans:
        for item in rp.items:
            if item.kind != "worktree":
                continue
            try:
                gitops.remove_worktree(_worktree_repo(item), Path(item.target))
            except (gitops.GitError, OSError) as e:
                failed(item, e)
                continue
            done(rp, item)
    for rp in plans:
        branch_items = sorted((i for i in rp.items if i.kind in _BRANCH_ORDER), key=lambda i: _BRANCH_ORDER[i.kind])
        for item in branch_items:
            b = item.branch or item.target
            if (item.repo, item.slug, b) in skip:
                continue
            try:
                repo = repo_of(item.repo)
                if item.kind == "pr":
                    forge.close(repo, item.number, COMMENT.format(repo=rp.repo, slug=rp.slug, why=rp.why))
                elif item.kind == "local_branch":
                    gitops.delete_branch(repo, b)
                else:
                    gitops.delete_remote_branch(repo, b)
            except Exception as e:  # git, the forge, or anything they raise: this branch stays, the rest goes on
                errors.append((item.key(), str(e)))
                skip.add((item.repo, item.slug, b))
                continue
            done(rp, item)
    root = paths.worktrees_dir().resolve()
    folders = [(rp, i) for rp in plans for i in rp.items if i.kind == "folder"]
    for rp, item in sorted(folders, key=lambda f: len(Path(f[1].target).parts), reverse=True):
        path = Path(item.target)
        try:
            if path.resolve() == root or root not in path.resolve().parents:
                raise OSError(f"{path} is not a folder under {root}")
            path.rmdir()   # only ever an empty folder
        except OSError as e:
            errors.append((item.key(), str(e)))
            continue
        done(rp, item)
    return {"removed": removed, "errors": errors, "held": held}
