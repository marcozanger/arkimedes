# Arkimedes always-on — P2: the runner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Marco leaves `ark-loop` running in a terminal. When a plan is approved with `ark approve`, the loop notices within seconds, creates `ark/<slug>` in that repo, runs every job with a headless Claude worker in its own worktree, gates, reviews and integration-tests it, squash-merges it into `ark/<slug>`, and at the end commits a decisions file and opens the final PR to the default branch. Workers cannot leave their area, read plans, push or merge.

**Architecture:** Scripts first (§10). `ark-loop` is a Python state machine with no model: every cycle it reads run state and receipt files, starts detached processes (worker, gate, reviewer, integration, ticks) and calls `ark-merge` in-process. Claude runs only as (a) workers and reviewers (`claude -p` with `worker-settings.json`: area-guard, read-guard, merge-guard, done-gate) and (b) two read-only judgment ticks: **judge** (opus) when a job attempt fails, **decisions** (sonnet) once per run at close. Every detached process writes a receipt file when it ends; the next cycle consumes it. PRs go through a `Forge` interface: `gh` for real, a local fake (`ARK_FORGE=local`) for tests.

**Tech Stack:** Python 3.14 + PyYAML + pytest in `~/.claude/arkimedes/.venv` (as P1); bash + jq for `done-gate.sh`; git, gh, Claude Code CLI 2.1.318 (`claude -p`).

**Spec:** `~/.claude/arkimedes/specs/2026-11-05-always-on-design.md` — this plan covers §4, §5, §6 (prevent; died/over-time detection; `ark-dispatch stop`), §7, §9 (decisions file), §10, and the matching §12 tests. Roadmap is in `specs/2026-11-05-p1-desk-contract-plan.md`.

**Decisions taken while planning (Marco, 2026-11-05):**
- **Scripts first.** All run mechanics (start run, dispatch workers and reviewers, gates, merges, tier escalation rules, close) are code in `ark-loop`. Claude ticks only judge a failed attempt (retry / escalate / block, with a note) and write the decisions file. This narrows §4's tick kinds to `judge` and `decisions`; §10 already asks for it ("mechanics are scripts").
- **Polling, not fswatch.** `ark-loop` polls a cheap file signature (approvals, receipts, plan files) every `poll_seconds` (15 s) and runs a full cycle every `tick_minutes` anyway. No new dependency.
- **Smoke on a new private repo `ark-sandbox`** (Task 15).

**Decisions taken by the planner (flag them in the plan review):**
- `claude` 2.1.318 has no `--max-turns`. The hard caps are `--max-budget-usd` (the job's `budget.usd`) and a wall-clock deadline per process (T0 20 min, T1 60, T2 120, as §6) enforced by the loop, which kills the process group.
- Workers use `--append-system-prompt-file agents/ark-dev.md` (reviewers `ark-reviewer.md`) instead of `--agent`: the role files live in `~/.claude/arkimedes/agents/` and must not show up as agents in Marco's normal sessions.
- Workers never push or open PRs. The runner pushes `ark/<slug>-<job>` after a worker ends DONE, opens the PR after the job gate is green, and records gate, verdict and integration results itself (the model's reply never counts as a gate result).
- Worktrees live in `~/.claude/ark-worktrees/<repo>/<slug>/<job>` (`ARK_WORKTREES` overrides it), outside `~/.claude/arkimedes/` so `desk-guard`'s relative-path rule cannot misfire on a repo folder called `desk`.
- Escalation rules in code: any failed attempt counts; two failures at a tier allow `escalate`, not `retry`; T2 cannot escalate. An integration-gate failure goes back to the worker once, then the job is blocked (§5) — no judge needed. Three review rounds without CLEAN is a failure.
- Moved to P3: stalled / over-token detection, intervene ticks, `steer`, `ark ps/logs/attach`, `questions.md` + `ark answer`, phone notifications, the re-approval question. In P2 a plan that changes after approval holds the run (`Status: held`) and nothing new is dispatched; a worker that ends BLOCKED with a question goes to the judge, which may block the job with the question in its note.

**Open this plan's executing session in `~/.claude/arkimedes`** (`cd ~/.claude/arkimedes && claude`), as for P1.

---

## File structure

| Path (under `~/.claude/arkimedes/`) | Responsibility |
|---|---|
| `lib/ark/paths.py` (modify) | Add `code_dir`, `worktrees_dir`, `run_dir`, workspace defaults (`limits`, `tick_minutes`, `poll_seconds`) |
| `lib/ark/globs.py` (modify) | Add `match` / `match_any`: does a file path match an area glob |
| `lib/ark/plan.py` (modify) | Add `task_texts`: `### T<n>` sections of `## Tasks` by id |
| `lib/ark/run.py` | Run state: `runs/<repo>/<slug>/state.json` (machine) + `runs/<repo>/<slug>.md` (people, `Status:` line) |
| `lib/ark/gitops.py` | git helpers: default branch, fetch, push to `ark/*` only, worktrees, changed files, squash, commit a file on a branch |
| `lib/ark/forge.py` | PRs: `GhForge` (gh) and `LocalForge` (tests, `ARK_FORGE=local`) |
| `lib/ark/proc.py` | Detached processes: start, receipt, reap (died / over time), stop, consume; `ark-dispatch stop` |
| `lib/ark/claude.py` | `claude -p` command lines and environments per role; tiers, models, deadlines |
| `lib/ark/brief.py` | Worker / reviewer briefs, judge and decisions prompts, PR bodies, output parsers |
| `lib/ark/merge.py` | §7 merge checks, `ark-merge` |
| `lib/ark/integrate.py` | `ark-integrate`: temporary squash of the PR head onto `ark/<slug>` + `integration_gate` |
| `lib/ark/loop.py` | `ark-loop`: approvals → runs, job state machine, limits, close, polling main |
| `agents/ark-dev.md`, `agents/ark-reviewer.md` | Default worker and reviewer roles (appended system prompts) |
| `worker-settings.json` | Settings for every runner Claude process: guards, `ARKIMEDES=1` |
| `hooks/area-guard.py`, `hooks/read-guard.py`, `hooks/merge-guard.py`, `hooks/done-gate.sh` | Worker guards (§6, §7) |
| `hooks/session-start.sh` (modify) | Inject nothing for runner processes |
| `bin/ark-loop`, `bin/ark-merge`, `bin/ark-integrate`, `bin/ark-dispatch` | Thin wrappers |
| `workspace.json` (modify) | `limits`, `tick_minutes`, `poll_seconds` |
| `tests/fake_claude.py` | Stand-in for `claude -p` in tests |
| `tests/conftest.py` (modify) | Git identity, `runner` fixture (seeded repos, fake claude, local forge) |
| `tests/test_*.py` | One test file per module, plus `test_loop.py` end to end |

Outside the folder: `~/.claude/agents/arkimedes.md` (always-on pointer), `~/.claude/projects/-Users-marcozanger-Documents-workspace-humanharness/memory/push-permission.md` (note), `~/.local/bin/ark-loop` (symlink).

**State machine of a job** (`state.json` → `jobs.<id>.status`):

```
pending ──(after jobs merged, writer slot)──▶ working ──DONE, commits in area, clean──▶ needs_gate ─▶ gating
                                                │                                                   │ green: open PR
                                                └─FAILED/BLOCKED/timeout─▶ needs_judge ◀─red────────┘
needs_review ─▶ reviewing ──CLEAN@head──▶ needs_integrate ─▶ integrating ──green──▶ ark-merge ─▶ merged
                    │ FIX (round < 3)                            │ red: 1st → needs_worker (resume), 2nd → blocked
                    └──────────▶ needs_worker (resume) ◀─────────┘   base moved → needs_integrate
needs_judge ─▶ judging ──retry/escalate──▶ needs_worker ─▶ working          block ─▶ blocked
```

A run (`state.json` → `status`): `waiting` (pre-check failed, retried every cycle) → `running` → `done` (all merged) or `blocked` (any job blocked); `held` when the plan changed after approval.

Conventions: as P1 — code, comments, prompts and files in English; CLI text meant for Marco in Spanish. Tests: `.venv/bin/python -m pytest -q` from `~/.claude/arkimedes`. Every commit message ends with the attribution lines from your context.

---

### Task 1: Workspace defaults and runner paths

**Files:**
- Modify: `lib/ark/paths.py`, `workspace.json`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_paths.py`:

```python
import json


def test_workspace_defaults_fill_missing_keys(ark_home):
    ws = paths.load_workspace()
    assert ws["limits"] == {"writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1}
    assert ws["tick_minutes"] == 15 and ws["poll_seconds"] == 15


def test_workspace_partial_limits_keep_other_defaults(ark_home):
    (ark_home / "workspace.json").write_text(json.dumps({"roots": [], "limits": {"writers_total": 1}}))
    ws = paths.load_workspace()
    assert ws["limits"]["writers_total"] == 1
    assert ws["limits"]["writers_per_repo"] == 3
    assert paths.load_workspace()["limits"]["writers_total"] == 1  # defaults never mutated


def test_runner_paths(ark_home, tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_WORKTREES", str(tmp_path / "wt"))
    assert paths.run_dir("muscleapp/mobile", "x") == ark_home / "runs/muscleapp/mobile/x"
    assert paths.worktrees_dir() == tmp_path / "wt"
    assert (paths.code_dir() / "lib/ark/paths.py").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_paths.py`
Expected: FAIL — `KeyError: 'limits'` and `AttributeError: ... 'run_dir'`.

- [ ] **Step 3: Implement** — in `lib/ark/paths.py` add `import copy` to the imports, replace `load_workspace` and add the new helpers:

```python
DEFAULTS = {
    "roots": [],
    "exclude": [],
    "limits": {"writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1},
    "tick_minutes": 15,
    "poll_seconds": 15,
}


def code_dir() -> Path:
    """Where Arkimedes' code lives (bin/, lib/, hooks/, agents/): this checkout."""
    return Path(__file__).resolve().parents[2]


def worktrees_dir() -> Path:
    return Path(os.environ.get("ARK_WORKTREES") or Path.home() / ".claude" / "ark-worktrees").expanduser()


def run_dir(repo: str, slug: str) -> Path:
    return runs_dir() / repo / slug


def load_workspace() -> dict:
    f = ark_home() / "workspace.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    ws = copy.deepcopy(DEFAULTS)
    ws.update({k: v for k, v in data.items() if k != "limits"})
    ws["limits"].update(data.get("limits") or {})
    return ws
```

Set `workspace.json` to:

```json
{
  "roots": ["~/Documents/workspace"],
  "exclude": [],
  "limits": { "writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1 },
  "tick_minutes": 15,
  "poll_seconds": 15
}
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest`
Expected: all pass (P1 tests included).

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/paths.py workspace.json tests/test_paths.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): workspace limits and runner paths"
```

---

### Task 2: Glob matching and task texts

**Files:**
- Modify: `lib/ark/globs.py`, `lib/ark/plan.py`
- Test: `tests/test_globs.py`, `tests/test_plan.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_globs.py`:

```python
from ark.globs import match, match_any


def test_match_double_star():
    assert match("src/shell/a.py", "src/shell/**")
    assert match("src/shell/deep/b.py", "src/shell/**")
    assert not match("src/ui/a.py", "src/shell/**")
    assert match("a/b/c.test.ts", "**/*.test.ts")
    assert match("c.test.ts", "**/*.test.ts")


def test_match_single_star_stays_in_one_segment():
    assert match("lib/x.dart", "lib/*.dart")
    assert not match("lib/sub/x.dart", "lib/*.dart")


def test_literal_folder_matches_files_inside():
    assert match("docs/a.md", "docs")
    assert match("docs/a.md", "docs/")
    assert not match("docsx/a.md", "docs")
    assert match("README.md", "./README.md")


def test_match_any():
    assert match_any("src/ui/a.tsx", ["src/shell/**", "src/ui/**"])
    assert not match_any("x", [])
```

Append to `tests/test_plan.py`:

```python
from ark.plan import load_plan, task_texts


def test_task_texts_by_id(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("---\nstatus: draft\n---\n# X\n\n## Tasks\n\nintro\n\n### T1 — first\n\nDo one.\n\n"
                 "### T2 — second\n\nDo two.\n\n## PR plan\n\nx\n")
    t = task_texts(load_plan(p))
    assert list(t) == ["T1", "T2"]
    assert t["T1"] == "### T1 — first\n\nDo one."
    assert t["T2"].startswith("### T2 — second") and "Do two." in t["T2"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_globs.py tests/test_plan.py`
Expected: FAIL — `ImportError: cannot import name 'match'` / `'task_texts'`.

- [ ] **Step 3: Implement**

Append to `lib/ark/globs.py` (add `import re` at the top):

```python
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
```

Append to `lib/ark/plan.py`:

```python
TASK_SPLIT = re.compile(r"^(### (T\d+)\b[^\n]*)\n?", re.M)


def task_texts(plan: Plan) -> dict[str, str]:
    """Each `### T<n>` section of `## Tasks`, heading included, by task id."""
    parts = TASK_SPLIT.split(plan.sections.get("Tasks", ""))
    return {parts[i + 1]: (parts[i] + "\n" + parts[i + 2]).strip() for i in range(1, len(parts), 3)}
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_globs.py tests/test_plan.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/globs.py lib/ark/plan.py tests/test_globs.py tests/test_plan.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): area glob matching and task texts"
```

---

### Task 3: Run state

**Files:**
- Create: `lib/ark/run.py`, `tests/test_run.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_run.py`:

```python
from ark import desk, paths, run

APPROVAL = {"repo": "demo", "slug": "notes-edit", "plan": "/x/plan.md", "sha256": "ab" * 32,
            "approved_at": "2026-11-05T10:00:00+00:00"}


def test_new_state_is_waiting_without_jobs(ark_home):
    st = run.new_state(APPROVAL)
    assert st["status"] == "waiting" and st["jobs"] == {}
    assert st["plan_sha"] == "ab" * 32


def test_save_writes_state_and_run_file(ark_home):
    st = run.new_state(APPROVAL)
    st["status"] = "running"
    st["jobs"]["shell"] = run.new_job({"id": "shell", "tier": "T1"})
    run.event(st, "run started")
    run.save(st)
    assert run.load("demo", "notes-edit")["jobs"]["shell"]["status"] == "pending"
    md = paths.run_file("demo", "notes-edit").read_text()
    assert "Status: running" in md and "| shell | pending | T1 |" in md and "run started" in md
    assert desk._run("demo", "notes-edit") == "running"   # ark status reads the same line


def test_load_missing_is_none(ark_home):
    assert run.load("demo", "nope") is None


def test_events_are_capped(ark_home):
    st = run.new_state(APPROVAL)
    for i in range(250):
        run.event(st, f"e{i}")
    assert len(st["events"]) == run.MAX_EVENTS and st["events"][-1].endswith("e249")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_run.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'ark.run'`.

- [ ] **Step 3: Implement** — `lib/ark/run.py`:

```python
"""Run state of one plan.

runs/<repo>/<slug>/state.json is the machine state (only ark-loop writes it);
runs/<repo>/<slug>.md is rendered from it for people and for `ark status` (its `Status:` line).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import paths

MAX_EVENTS = 200
TERMINAL = {"merged", "blocked"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_state(approval: dict) -> dict:
    return {
        "repo": approval["repo"], "slug": approval["slug"], "plan": approval["plan"],
        "plan_sha": approval["sha256"], "approved_at": approval["approved_at"],
        "status": "waiting", "reason": "", "default_branch": None, "base_sha": None,
        "started": now(), "updated": now(), "closing": None, "final_pr": None,
        "events": [], "alerts": [], "jobs": {},
    }


def new_job(mj: dict) -> dict:
    return {
        "status": "pending", "tier": mj["tier"], "attempts": 0, "tier_attempts": 0,
        "session": None, "review_rounds": 0, "integrate_fails": 0, "base": None, "head": None,
        "pr": None, "action": None, "failure": None, "setup_done": False, "reason": "",
        "tier_path": [mj["tier"]], "heavy": bool(mj.get("heavy", False)), "note": None,
        "resume_msg": None, "review_errors": 0, "usd": 0.0,
    }


def state_file(repo: str, slug: str) -> Path:
    return paths.run_dir(repo, slug) / "state.json"


def load(repo: str, slug: str) -> dict | None:
    f = state_file(repo, slug)
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def event(st: dict, text: str) -> None:
    st["events"].append(f"{now()} · {text}")
    del st["events"][:-MAX_EVENTS]


def alert(st: dict, text: str) -> None:
    """Something Marco must see: ark-loop prints it once (phone notifications are P3)."""
    st["alerts"].append(f"{st['repo']}/{st['slug']}: {text}")
    event(st, "ALERT " + text)


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save(st: dict) -> None:
    st["updated"] = now()
    _atomic(state_file(st["repo"], st["slug"]), json.dumps(st, indent=2) + "\n")
    _atomic(paths.run_file(st["repo"], st["slug"]), render(st))


def render(st: dict) -> str:
    lines = [
        f"# Run {st['repo']}/{st['slug']}",
        "",
        f"Status: {st['status']}",
        f"Plan: {st['plan']} · sha256 {st['plan_sha'][:12]}",
    ]
    if st.get("base_sha"):
        lines.append(f"Integration branch: ark/{st['slug']} (from {st['default_branch']} @ {st['base_sha'][:8]})")
    lines.append(f"Started: {st['started']} · Updated: {st['updated']}")
    if st.get("reason"):
        lines.append(f"Reason: {st['reason']}")
    if st.get("final_pr"):
        lines.append(f"Final PR: {st['final_pr']['url']}")
    if st["jobs"]:
        lines += ["", "| job | status | tier | attempts | PR | head | note |", "|---|---|---|---|---|---|---|"]
        for jid, j in st["jobs"].items():
            pr = j["pr"]["url"] if j.get("pr") else "-"
            head = (j.get("head") or "-")[:8]
            lines.append(f"| {jid} | {j['status']} | {j['tier']} | {j['attempts']} | {pr} | {head} | {j.get('reason') or ''} |")
    lines += ["", "## Events", ""] + [f"- {e}" for e in st["events"]]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_run.py`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/run.py tests/test_run.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): run state file and readable run file"
```

---

### Task 4: Test fixtures for the runner, and git operations

**Files:**
- Modify: `tests/conftest.py`, `tests/helpers.py`
- Create: `lib/ark/gitops.py`, `tests/test_gitops.py`

- [ ] **Step 1: Add the fixtures** — append to `tests/conftest.py`:

```python
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                 "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def runner(ark_home, make_repo, tmp_path, monkeypatch):
    """Runner test setup: fake claude, local forge, worktrees under tmp. Returns seed(name) that
    creates a repo with one commit on master, pushed, origin/HEAD set."""
    monkeypatch.setenv("ARK_WORKTREES", str(tmp_path / "wt"))
    monkeypatch.setenv("ARK_FORGE", "local")
    monkeypatch.setenv("ARK_CLAUDE", str(CODE / "tests" / "fake_claude.py"))
    (tmp_path / "fake").mkdir()
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "fake"))
    for k in ("FAKE_FAIL", "FAKE_OUTSIDE", "FAKE_FIX", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    def seed(name="demo"):
        repo = make_repo(name)
        (repo / "README.md").write_text(f"{name}\n")
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", "init", cwd=repo)
        git("push", "-q", "origin", "master", cwd=repo)
        git("remote", "set-head", "origin", "master", cwd=repo)
        return repo
    return seed
```

Append to `tests/helpers.py`:

```python
import json
from pathlib import Path


def approve(path, repo="demo", slug="notes-edit", at="2026-11-05T10:00:00+00:00"):
    """What `ark approve` leaves behind, without the terminal prompt."""
    from ark import paths
    from ark.plan import set_status, sha256_file
    set_status(path, "closed")
    f = paths.approval_file(repo, slug)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"repo": repo, "slug": slug, "plan": str(Path(path).resolve()),
                             "sha256": sha256_file(path), "approved_at": at}))
    return f
```

- [ ] **Step 2: Write the failing tests** — `tests/test_gitops.py`:

```python
import subprocess

import pytest

from ark import gitops


def test_default_branch_and_rev(runner):
    repo = runner()
    assert gitops.default_branch(repo) == "master"
    assert gitops.rev(repo, "origin/master") == gitops.rev(repo, "HEAD")


def test_push_only_to_ark_branches(runner):
    repo = runner()
    sha = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, sha, "ark/x")
    assert gitops.remote_sha(repo, "ark/x") == sha
    assert gitops.remote_sha(repo, "ark/none") is None
    with pytest.raises(gitops.GitError):
        gitops.push_ref(repo, sha, "master")


def test_worktree_changes_and_squash(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, base, "ark/x")
    gitops.fetch(repo)
    wt = tmp_path / "wt" / "job"
    gitops.add_worktree(repo, wt, "ark/x-job", "origin/ark/x")
    (wt / "src").mkdir()
    (wt / "src" / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", "a"], check=True)
    assert gitops.is_clean(wt)
    head = gitops.rev(wt, "HEAD")
    assert gitops.changed_files(repo, base, head) == ["src/a.txt"]
    gitops.push_branch(wt, "ark/x-job")
    gitops.fetch(repo)
    sha = gitops.squash(repo, "origin/ark/x", "origin/ark/x-job", "squash", tmp_path / "wt" / "scratch")
    assert sha and gitops.changed_files(repo, base, sha) == ["src/a.txt"]
    assert not (tmp_path / "wt" / "scratch").exists()


def test_squash_conflict_returns_none(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    for branch, text in (("ark/a", "one\n"), ("ark/b", "two\n")):
        wt = tmp_path / branch.replace("/", "-")
        gitops.add_worktree(repo, wt, branch, base)
        (wt / "README.md").write_text(text)
        subprocess.run(["git", "-C", str(wt), "commit", "-qam", branch], check=True)
        gitops.push_branch(wt, branch)
    gitops.fetch(repo)
    assert gitops.squash(repo, "origin/ark/a", "origin/ark/b", "x", tmp_path / "scratch") is None


def test_commit_file_on_branch(runner, tmp_path):
    repo = runner()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), "ark/x")
    sha = gitops.commit_file(repo, "ark/x", "docs/decisions/x.md", "# D\n", "docs: d", tmp_path / "scratch")
    assert gitops.remote_sha(repo, "ark/x") == sha
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gitops.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'ark.gitops'`.

- [ ] **Step 4: Implement** — `lib/ark/gitops.py`:

```python
"""git for the runner. It only ever pushes to ark/* branches (never the default branch, never force)."""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def git(cwd, *args, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise GitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def fetch(repo) -> None:
    git(repo, "fetch", "-q", "--prune", "origin")


def default_branch(repo) -> str:
    """The branch origin/HEAD points to (master or main)."""
    for attempt in range(2):
        ref = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
        if ref.startswith("origin/"):
            return ref.removeprefix("origin/")
        if attempt == 0:
            git(repo, "remote", "set-head", "origin", "--auto", check=False)
    raise GitError("cannot tell the default branch: origin/HEAD is not set")


def rev(cwd, ref: str) -> str:
    return git(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")


def remote_sha(repo, branch: str) -> str | None:
    out = git(repo, "ls-remote", "origin", f"refs/heads/{branch}")
    return out.split()[0] if out else None


def _ark_only(branch: str) -> None:
    if not branch.startswith("ark/"):
        raise GitError(f"the runner only pushes ark/* branches, not {branch!r}")


def push_ref(repo, sha: str, branch: str) -> None:
    _ark_only(branch)
    git(repo, "push", "-q", "origin", f"{sha}:refs/heads/{branch}")


def push_branch(worktree, branch: str) -> None:
    _ark_only(branch)
    git(worktree, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")


def add_worktree(repo, path: Path, branch: str, start: str) -> None:
    """A worktree on `branch`; the branch is created at `start` when it does not exist yet."""
    path = Path(path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
        git(repo, "worktree", "add", "-q", str(path), branch)
    else:
        git(repo, "worktree", "add", "-q", "-b", branch, str(path), start)


def remove_worktree(repo, path: Path) -> None:
    git(repo, "worktree", "remove", "--force", str(path), check=False)
    git(repo, "worktree", "prune", check=False)


def is_clean(worktree) -> bool:
    return git(worktree, "status", "--porcelain") == ""


def changed_files(repo, base: str, head: str) -> list[str]:
    out = git(repo, "diff", "--name-only", f"{base}...{head}")
    return [line for line in out.splitlines() if line]


def squash_keep(repo, base_ref: str, head_ref: str, message: str, scratch: Path) -> str | None:
    """Squash `head_ref` onto `base_ref` in a detached scratch worktree and commit; the new sha,
    or None on a conflict. The scratch worktree stays: the caller must remove_worktree() it."""
    scratch = Path(scratch)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--detach", str(scratch), base_ref)
    r = subprocess.run(["git", "-C", str(scratch), "merge", "--squash", head_ref],
                       capture_output=True, text=True)
    if r.returncode:
        return None
    if subprocess.run(["git", "-C", str(scratch), "diff", "--cached", "--quiet"]).returncode:
        git(scratch, "commit", "-q", "-m", message)
    return rev(scratch, "HEAD")


def squash(repo, base_ref: str, head_ref: str, message: str, scratch: Path) -> str | None:
    """squash_keep, then remove the scratch worktree; nothing is pushed."""
    try:
        return squash_keep(repo, base_ref, head_ref, message, scratch)
    finally:
        remove_worktree(repo, scratch)


def commit_file(repo, branch: str, rel: str, content: str, message: str, scratch: Path) -> str:
    """Commit one file on top of origin/<branch> and push it there."""
    _ark_only(branch)
    fetch(repo)
    scratch = Path(scratch)
    remove_worktree(repo, scratch)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "--detach", str(scratch), f"origin/{branch}")
    try:
        f = scratch / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        git(scratch, "add", rel)
        git(scratch, "commit", "-q", "-m", message)
        push_branch(scratch, branch)
        return rev(scratch, "HEAD")
    finally:
        remove_worktree(repo, scratch)
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gitops.py`
Expected: `5 passed`. (`fake_claude.py` does not exist yet; the `runner` fixture only stores its path.)

- [ ] **Step 6: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/gitops.py tests/test_gitops.py tests/conftest.py tests/helpers.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): git operations for the runner (ark/* pushes only)"
```

---

### Task 5: Forge — PRs through gh, or locally in tests

**Files:**
- Create: `lib/ark/forge.py`, `tests/test_forge.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_forge.py`:

```python
import subprocess

import pytest

from ark import forge, gitops


def branch_with_file(repo, tmp_path, branch, base, name):
    wt = tmp_path / branch.replace("/", "-")
    gitops.add_worktree(repo, wt, branch, base)
    (wt / name).write_text("x\n")
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", name], check=True)
    gitops.push_branch(wt, branch)
    return gitops.rev(wt, "HEAD")


def test_get_picks_local_in_tests(runner):
    assert isinstance(forge.get(), forge.LocalForge)


def test_local_create_view_merge(runner, tmp_path):
    repo = runner()
    base = gitops.rev(repo, "HEAD")
    gitops.push_ref(repo, base, "ark/x")
    head = branch_with_file(repo, tmp_path, "ark/x-job", base, "a.txt")
    f = forge.LocalForge()
    pr = f.create(repo, base="ark/x", head="ark/x-job", title="feat: a", body="b")
    v = f.view(repo, pr["number"])
    assert (v["base"], v["head"], v["head_sha"], v["state"]) == ("ark/x", "ark/x-job", head, "OPEN")
    with pytest.raises(forge.ForgeError):
        f.merge(repo, pr["number"], subject="feat: a", head_sha=base)   # head moved → refused
    f.merge(repo, pr["number"], subject="feat: a", head_sha=head)
    gitops.fetch(repo)
    assert gitops.changed_files(repo, base, "origin/ark/x") == ["a.txt"]
    assert f.view(repo, pr["number"])["state"] == "MERGED"
    assert [p["number"] for p in f.list(repo)] == [pr["number"]]


def test_gh_forge_builds_commands(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = "https://github.com/m/r/pull/7\n" if cmd[:3] == ["gh", "pr", "create"] else \
            '{"baseRefName":"ark/x","headRefName":"ark/x-j","headRefOid":"abc","state":"OPEN","url":"u"}'
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(forge.subprocess, "run", fake_run)
    g = forge.GhForge()
    assert g.create("/r", base="ark/x", head="ark/x-j", title="t", body="b") == \
        {"number": 7, "url": "https://github.com/m/r/pull/7"}
    assert g.view("/r", 7)["head_sha"] == "abc"
    g.merge("/r", 7, subject="t", head_sha="abc")
    assert calls[-1] == ["gh", "pr", "merge", "7", "--squash", "--subject", "t", "--body", "",
                         "--match-head-commit", "abc"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_forge.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'ark.forge'`.

- [ ] **Step 3: Implement** — `lib/ark/forge.py`:

```python
"""Pull requests. GhForge uses Marco's gh; LocalForge (ARK_FORGE=local) keeps PRs in a json file
inside the repo's .git and squash-merges with plain git, for tests and offline sandboxes."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from . import gitops, paths


class ForgeError(Exception):
    pass


class GhForge:
    def _gh(self, repo, *args, input: str | None = None) -> str:
        r = subprocess.run(["gh", *args], cwd=str(repo), capture_output=True, text=True, input=input)
        if r.returncode:
            raise ForgeError(f"gh {' '.join(args[:3])}: {(r.stderr or r.stdout).strip()}")
        return r.stdout.strip()

    def create(self, repo, *, base: str, head: str, title: str, body: str) -> dict:
        url = self._gh(repo, "pr", "create", "--base", base, "--head", head, "--title", title,
                       "--body-file", "-", input=body).splitlines()[-1].strip()
        return {"number": int(url.rstrip("/").rsplit("/", 1)[1]), "url": url}

    def view(self, repo, number: int) -> dict:
        d = json.loads(self._gh(repo, "pr", "view", str(number), "--json",
                                "baseRefName,headRefName,headRefOid,state,url"))
        return {"base": d["baseRefName"], "head": d["headRefName"], "head_sha": d["headRefOid"],
                "state": d["state"], "url": d["url"]}

    def merge(self, repo, number: int, *, subject: str, head_sha: str) -> None:
        self._gh(repo, "pr", "merge", str(number), "--squash", "--subject", subject, "--body", "",
                 "--match-head-commit", head_sha)


class LocalForge:
    def _file(self, repo) -> Path:
        return Path(repo) / ".git" / "ark-local-prs.json"

    def list(self, repo) -> list[dict]:
        f = self._file(repo)
        return json.loads(f.read_text()) if f.exists() else []

    def _save(self, repo, prs: list[dict]) -> None:
        self._file(repo).write_text(json.dumps(prs, indent=2))

    def create(self, repo, *, base: str, head: str, title: str, body: str) -> dict:
        prs = self.list(repo)
        n = len(prs) + 1
        pr = {"number": n, "url": f"local://{Path(repo).name}/pull/{n}", "base": base, "head": head,
              "title": title, "body": body, "state": "OPEN"}
        self._save(repo, prs + [pr])
        return {"number": n, "url": pr["url"]}

    def view(self, repo, number: int) -> dict:
        pr = self.list(repo)[number - 1]
        return {"base": pr["base"], "head": pr["head"], "head_sha": gitops.remote_sha(repo, pr["head"]),
                "state": pr["state"], "url": pr["url"]}

    def merge(self, repo, number: int, *, subject: str, head_sha: str) -> None:
        prs = self.list(repo)
        pr = prs[number - 1]
        if gitops.remote_sha(repo, pr["head"]) != head_sha:
            raise ForgeError("head moved")
        gitops.fetch(repo)
        scratch = paths.worktrees_dir() / "_forge" / uuid.uuid4().hex
        try:
            sha = gitops.squash_keep(repo, f"origin/{pr['base']}", f"origin/{pr['head']}", subject, scratch)
            if sha is None:
                raise ForgeError("merge conflict")
            gitops.push_branch(scratch, pr["base"])
        finally:
            gitops.remove_worktree(repo, scratch)
        pr["state"] = "MERGED"
        self._save(repo, prs)


def get():
    return LocalForge() if os.environ.get("ARK_FORGE") == "local" else GhForge()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_forge.py`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/forge.py tests/test_forge.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): forge interface (gh, local for tests)"
```

---

### Task 6: Detached processes and receipts

**Files:**
- Create: `lib/ark/proc.py`, `tests/test_proc.py`

Every process the runner starts (worker, gate, reviewer, integration check, tick) runs under a small wrapper (`python -m ark.proc run …`) in its own process group, so it outlives the cycle that started it and `ark-loop` can be restarted at any time. The wrapper runs optional setup commands, then the command, and writes `<name>.receipt.json` when it ends. The next cycle consumes the receipt.

- [ ] **Step 1: Write the failing tests** — `tests/test_proc.py`:

```python
import json
import os
import signal
import sys
import time

from ark import paths, proc


def wait_receipt(d, name, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if name in proc.finished(d):
            return
        time.sleep(0.05)
    raise AssertionError(f"no receipt for {name}")


def py(code):
    return [sys.executable, "-c", code]


def test_receipt_reads_claude_result_line(tmp_path):
    line = json.dumps({"type": "result", "result": "hi\nSTATUS: DONE", "session_id": "s1",
                       "total_cost_usd": 0.5, "is_error": False})
    proc.start(tmp_path, "j.worker", py(f"print('noise'); print({line!r})"), cwd=tmp_path,
               env=dict(os.environ), minutes=1)
    assert proc.active(tmp_path) == ["j.worker"]
    wait_receipt(tmp_path, "j.worker")
    r = proc.consume(tmp_path, "j.worker")
    assert (r["exit"], r["result"], r["session"], r["usd"]) == (0, "hi\nSTATUS: DONE", "s1", 0.5)
    assert proc.active(tmp_path) == []
    assert list((tmp_path / "done").glob("j.worker-*.json")) and list((tmp_path / "done").glob("j.worker-*.log"))


def test_plain_command_gives_its_tail_and_exit(tmp_path):
    proc.start(tmp_path, "j.gate", ["sh", "-c", "echo red; exit 3"], cwd=tmp_path, env=dict(os.environ), minutes=1)
    wait_receipt(tmp_path, "j.gate")
    r = proc.consume(tmp_path, "j.gate")
    assert r["exit"] == 3 and "red" in r["result"] and r["reason"] == ""


def test_failed_setup_skips_the_command(tmp_path):
    marker = tmp_path / "ran"
    proc.start(tmp_path, "j.worker", ["touch", str(marker)], cwd=tmp_path, env=dict(os.environ),
               minutes=1, pre=["exit 4"])
    wait_receipt(tmp_path, "j.worker")
    r = proc.consume(tmp_path, "j.worker")
    assert r["exit"] == 4 and r["reason"] == "setup failed: exit 4" and not marker.exists()


def test_pythonpath_does_not_leak_into_the_command(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc.start(tmp_path, "j.x", py("import os; print(os.environ.get('PYTHONPATH', '-'))"), cwd=tmp_path,
               env=env, minutes=1)
    wait_receipt(tmp_path, "j.x")
    assert proc.consume(tmp_path, "j.x")["result"].strip() == "-"


def test_over_time_is_stopped(tmp_path):
    proc.start(tmp_path, "j.worker", ["sleep", "30"], cwd=tmp_path, env=dict(os.environ), minutes=0.001)
    time.sleep(0.2)
    assert proc.overdue(tmp_path) == ["j.worker"]
    assert proc.reap(tmp_path) == ["j.worker"]
    r = proc.consume(tmp_path, "j.worker")
    assert r["reason"].startswith("timeout")


def test_died_without_receipt(tmp_path):
    info = proc.start(tmp_path, "j.worker", ["sleep", "30"], cwd=tmp_path, env=dict(os.environ), minutes=5)
    time.sleep(0.3)
    os.kill(info["pid"], signal.SIGKILL)          # the wrapper dies; no receipt is written
    time.sleep(0.2)
    try:
        assert proc.reap(tmp_path) == ["j.worker"]
        assert proc.consume(tmp_path, "j.worker")["reason"] == "died without a receipt"
    finally:
        os.killpg(info["pid"], signal.SIGKILL)    # the orphaned sleep


def test_dispatch_stop(ark_home):
    d = paths.run_dir("demo", "x")
    proc.start(d, "shell.worker", ["sleep", "30"], cwd=d.parent, env=dict(os.environ), minutes=5)
    time.sleep(0.3)
    assert proc.main(["stop", "demo/x", "shell"]) == 0
    assert proc.consume(d, "shell.worker")["reason"] == "stopped by Marco"
    assert proc.main(["stop", "demo/x", "shell"]) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_proc.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'ark.proc'`.

- [ ] **Step 3: Implement** — `lib/ark/proc.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_proc.py`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/proc.py tests/test_proc.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): detached processes with receipts, timeouts and stop"
```

---

### Task 7: Briefs, tick prompts and agent roles

**Files:**
- Create: `lib/ark/brief.py`, `agents/ark-dev.md`, `agents/ark-reviewer.md`, `tests/test_brief.py`

A worker gets only its job: the job fields from the manifest and the text of its own tasks, never the plan file (§6 read-guard enforces the "never"). Ticks get only what their one decision needs (§10). No prompt contains a triple-backtick fence, so nothing breaks when a prompt is shown inside Markdown.

- [ ] **Step 1: Write the failing tests** — `tests/test_brief.py`:

```python
from helpers import job, manifest, plan_text

from ark import brief, run
from ark.plan import load_plan

APPROVAL = {"repo": "demo", "slug": "notes-edit", "plan": "/x/plan.md", "sha256": "ab" * 32,
            "approved_at": "2026-11-05T10:00:00+00:00"}


def setup(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text())
    plan = load_plan(p)
    st = run.new_state(APPROVAL)
    for mj in plan.manifest["jobs"]:
        st["jobs"][mj["id"]] = run.new_job(mj)
    return st, plan, {j["id"]: j for j in plan.manifest["jobs"]}


def test_worker_brief_has_only_its_job(tmp_path):
    st, plan, mj = setup(tmp_path)
    b = brief.worker_brief(st, "shell", mj["shell"], plan, worktree="/wt/shell", report="/r/shell.report.md")
    assert "src/shell/**" in b and "### T1 — step" in b and "### T2 — step" in b
    assert "### T3" not in b and "src/ui/**" not in b
    assert "/r/shell.report.md" in b and "STATUS: DONE" in b and "DECISIONS" in b
    assert "```" not in b


def test_worker_brief_adds_note_and_previous_report(tmp_path):
    st, plan, mj = setup(tmp_path)
    b = brief.worker_brief(st, "shell", mj["shell"], plan, worktree="/wt", report="/r",
                           note="use the store", previous_report="## What I did\n\nhalf of it")
    assert "use the store" in b and "half of it" in b


def test_reviewer_brief_asks_for_one_json_line(tmp_path):
    st, plan, mj = setup(tmp_path)
    b = brief.reviewer_brief(st, "ui", mj["ui"], plan, base="origin/ark/notes-edit", head="abc123")
    assert "git diff origin/ark/notes-edit...abc123" in b and '"verdict": "CLEAN"' in b
    assert "Includes" in b and "```" not in b


def test_worker_status():
    assert brief.worker_status("all good\nSTATUS: DONE") == ("DONE", "")
    assert brief.worker_status("x\nSTATUS: FAILED — tests red") == ("FAILED", "tests red")
    assert brief.worker_status("STATUS: BLOCKED - which store?") == ("BLOCKED", "which store?")
    assert brief.worker_status("no status")[0] == "FAILED"


def test_parse_json_line():
    assert brief.parse_json_line('text\n{"verdict": "CLEAN", "findings": []}') == {"verdict": "CLEAN", "findings": []}
    assert brief.parse_json_line('`{"action": "retry"}`') == {"action": "retry"}
    assert brief.parse_json_line("nothing") is None


def test_judge_prompt_lists_allowed_actions(tmp_path):
    st, plan, mj = setup(tmp_path)
    j = st["jobs"]["shell"]
    j["failure"] = "job gate red"
    p = brief.judge_prompt(st, "shell", j, mj["shell"], ["block", "retry"], "## What I did\n\nx")
    assert "retry, block" in p or "block, retry" in p
    assert "job gate red" in p and '"action"' in p


def test_decisions_prompt_and_fallback(tmp_path):
    st, plan, mj = setup(tmp_path)
    st["jobs"]["shell"]["status"] = "merged"
    st["jobs"]["ui"].update(status="blocked", reason="integration gate failed twice")
    reports = {"shell": "## DECISIONS\n\n- Kept notes in one file. Why: simpler.\n\n## Open issues\n\nnone"}
    p = brief.decisions_prompt(st, reports)
    assert "Kept notes in one file" in p and "<details><summary>más</summary>" in p and "```" not in p
    f = brief.fallback_decisions(st, reports)
    assert f.startswith("# Decisions — notes-edit")
    assert "Kept notes in one file" in f and "integration gate failed twice" in f and "Open issues" not in f


def test_pr_bodies(tmp_path):
    st, plan, mj = setup(tmp_path)
    st["jobs"]["shell"].update(status="merged", pr={"number": 1, "url": "u1"})
    assert "shell work" in brief.pr_body(st, "shell", mj["shell"], "## What I did\n\nx")
    body = brief.final_pr_body(st)
    assert body.startswith("**Decisions:** `docs/decisions/notes-edit.md`")
    assert "| shell | merged | u1 |" in body and "| ui | pending | - |" in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_brief.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'ark.brief'`.

- [ ] **Step 3: Implement** — `lib/ark/brief.py`:

```python
"""What the runner says to a model, and how it reads the answers.

Prompts are built from the plan and the run state: a worker sees only its job (never the plan
file) and a tick sees only what its one decision needs (§10). No prompt holds a triple-backtick
fence."""
from __future__ import annotations

import json
import re
from datetime import date

from .plan import Plan, task_texts

STATUS = re.compile(r"^\s*STATUS:\s*(DONE|FAILED|BLOCKED)\b[\s:—–-]*(.*)$", re.M)

DECISIONS_FORMAT = """# Decisions — <slug>

Plan: <plan path> · approved <YYYY-MM-DD> · closed <YYYY-MM-DD>
Jobs: <n> merged, <n> blocked · Decisions: <n> worker, <n> reviewer, <n> arkimedes

## <job id> — <merged | blocked>

- **D1** · <the decision in 1-2 sentences> · _<author: worker (<model>) | reviewer | arkimedes>_
  <details><summary>más</summary>

  Why: <the reason>
  Rejected: <the alternatives>
  See: <PR link> · report `runs/<repo>/<slug>/<job>.report.md`
  </details>
"""


def _bullets(items) -> str:
    return "\n".join(f"- {i}" for i in items) or "- (none)"


def _job_block(mj: dict, plan: Plan) -> list[str]:
    texts = task_texts(plan)
    tasks = "\n\n".join(texts.get(t, f"(task {t} is missing from the plan)") for t in mj["tasks"])
    return [
        f"PR title: {mj['pr_title']}",
        f"Includes: {mj['includes']}",
        f"Excludes: {mj['excludes']}",
        f"Area (the only files this job may create or change):\n{_bullets(mj['area'])}",
        f"Done when:\n{_bullets(mj['done'])}",
        f"Gate: `{mj['gate']}`",
        "Tasks:",
        tasks,
    ]


def _section(text: str, name: str) -> str:
    m = re.search(rf"^##\s*{name}\b[^\n]*\n(.*?)(?=^## |\Z)", text or "", re.M | re.S)
    return m.group(1).strip() if m else ""


def worker_brief(st: dict, job_id: str, mj: dict, plan: Plan, *, worktree, report,
                 note: str | None = None, previous_report: str | None = None) -> str:
    slug = st["slug"]
    parts = [
        f"You are the worker for job `{job_id}` of the plan `{slug}` in the repo `{st['repo']}`.",
        f"Plan goal: {plan.sections.get('Goal', '').strip()}",
        "## Your job",
        *_job_block(mj, plan),
        "## Rules",
        _bullets([
            f"Work only in {worktree}, on the branch already checked out there. Commit your work.",
            "Do not push, and do not create, review or merge PRs: the runner does that after you finish.",
            "Do not read ~/.claude/arkimedes/desk or ~/.claude/arkimedes/approvals: this brief is your whole plan.",
            "A change the job needs outside its area: do not make it; write it under Open issues and end BLOCKED.",
            "Write the test first when a task adds behaviour. Make the gate pass before you finish.",
            "Finish DONE only with everything committed (a clean `git status`).",
            f"If you are asked to integrate the current integration branch: `git fetch origin` and "
            f"`git merge origin/ark/{slug}`, fix, commit.",
        ]),
        "## Report",
        f"Before you finish, write {report} (overwrite it) with exactly these sections:",
        _bullets([
            "`## What I did` — 2-5 lines.",
            "`## Tests` — what you ran and the result.",
            "`## DECISIONS` — 0-3 bullets, only what a reviewer would ask about: a choice between real "
            "alternatives, an interpretation of the plan, an assumption filling a gap. Each: the decision "
            "in one sentence, then `Why:` and `Rejected:`.",
            "`## Open issues` — what is left, or `none`.",
        ]),
        "## How to finish",
        "The last line of your final message is exactly one of:\n"
        "STATUS: DONE\nSTATUS: FAILED — <one line: why>\nSTATUS: BLOCKED — <one concrete question for Marco>",
    ]
    if note:
        parts += ["## Note from the runner", note.strip()]
    if previous_report:
        parts += ["## Report of the previous attempt (its commits are already in the worktree)",
                  previous_report.strip()[-6000:]]
    return "\n\n".join(parts) + "\n"


RESUME = {
    "review": "The reviewer asked for changes. Fix these findings and commit:",
    "integrate": "Your branch passed its gate, but the integration gate failed on a temporary merge with "
                 "the current origin/ark/{slug}. Run `git fetch origin` and `git merge origin/ark/{slug}`, "
                 "fix the failure and commit. The failure:",
    "judge": "Your previous attempt did not finish. What went wrong, and the runner's note:",
}


def resume_message(kind: str, text: str, slug: str) -> str:
    return (RESUME[kind].format(slug=slug) + "\n\n" + text.strip() +
            "\n\nThen update your report and end with the STATUS line, as before.")


def reviewer_brief(st: dict, job_id: str, mj: dict, plan: Plan, *, base: str, head: str) -> str:
    return "\n\n".join([
        f"You review job `{job_id}` of the plan `{st['slug']}` in the repo `{st['repo']}`. Do not change any file.",
        f"The diff to review: `git diff {base}...{head}` (this worktree has {head} checked out).",
        "## What the job had to do",
        *_job_block(mj, plan),
        "## Check",
        _bullets([
            "Does the diff do what the tasks and the `Done when` items ask?",
            "Does it go beyond Includes, or touch anything in Excludes (extra features, refactors, "
            "unrelated files)? Each is a finding.",
            "Bugs, broken edge cases, new behaviour without a test.",
            "Ignore style a linter would catch.",
        ]),
        "## Answer",
        "Your final message ends with one line of JSON and nothing after it:",
        '{"verdict": "CLEAN", "findings": []}',
        'or {"verdict": "FIX", "findings": ["<file>:<line> — <what is wrong and what to do>"]}',
        "FIX only for what must change before this merges.",
    ]) + "\n"


def judge_prompt(st: dict, job_id: str, j: dict, mj: dict, allowed: list[str], report: str | None) -> str:
    return "\n\n".join([
        "A job attempt failed in an Arkimedes run. Decide the next step. You have no tools; decide from "
        "what is below.",
        f"Run: {st['repo']}/{st['slug']} · job `{job_id}` ({mj['pr_title']})",
        f"Tier now: {j['tier']} · failed attempts at this tier: {j['tier_attempts']} · "
        f"tiers so far: {' → '.join(j['tier_path'])}",
        f"Failure: {j['failure']}",
        "Worker report (may be missing or from an earlier attempt):\n" + ((report or "")[-4000:] or "(none)"),
        "Actions allowed now: " + ", ".join(allowed),
        _bullets([
            "retry — same tier, same session: a mistake the worker can fix (a red test, a missed step).",
            "escalate — a fresh worker one tier up: the job is harder than this tier handles.",
            "block — stop this job; its dependents hold. The plan is wrong or a person must decide: "
            "the note is the question for Marco.",
        ]),
        "Answer with one line of JSON and nothing else:",
        '{"action": "retry", "note": "<one or two sentences for the next attempt, or the question for Marco>"}',
    ]) + "\n"


def decisions_prompt(st: dict, reports: dict[str, str]) -> str:
    jobs = []
    for jid, j in st["jobs"].items():
        pr = f"PR {j['pr']['url']}" if j.get("pr") else "no PR"
        head = f"{jid} — {j['status']} · tiers {' → '.join(j['tier_path'])} · {pr}"
        if j.get("reason"):
            head += f" · reason: {j['reason']}"
        jobs.append(f"Job {head}\n\n" + ((reports.get(jid) or "(no report)")[-5000:]))
    return "\n\n".join([
        f"Write the decisions file for the finished Arkimedes run {st['repo']}/{st['slug']}. "
        "You have no tools; everything you need is below.",
        "Keep only key decisions: what a human reviewer would ask about — a choice between real "
        "alternatives, a deviation from or interpretation of the plan, an assumption filling a gap, a job "
        "blocked, escalated or failing integration and why. Not routine detail. About 1-2 per job. "
        "Number them D1, D2… across the file. Workers proposed candidates in their DECISIONS sections; "
        "add your own (author arkimedes) for blocks and escalations from the events.",
        "Reply with the file only, in English Markdown, starting with its `# ` line, in this format:",
        DECISIONS_FORMAT,
        f"Plan: {st['plan']} · approved {st['approved_at'][:10]} · closed {date.today().isoformat()}",
        "Run events:\n" + "\n".join(st["events"][-60:]),
        "Jobs and their reports:",
        *jobs,
    ]) + "\n"


def fallback_decisions(st: dict, reports: dict[str, str]) -> str:
    """Written by the runner when the decisions tick fails: the raw candidates, unfiltered."""
    merged = sum(j["status"] == "merged" for j in st["jobs"].values())
    blocked = sum(j["status"] == "blocked" for j in st["jobs"].values())
    lines = [f"# Decisions — {st['slug']}", "",
             f"Plan: {st['plan']} · approved {st['approved_at'][:10]} · closed {date.today().isoformat()}",
             f"Jobs: {merged} merged, {blocked} blocked · written by the runner (the decisions tick failed; "
             "candidates are unfiltered)", ""]
    for jid, j in st["jobs"].items():
        lines += [f"## {jid} — {j['status']}", ""]
        if j.get("reason"):
            lines += [f"- Blocked: {j['reason']} · _arkimedes_", ""]
        found = _section(reports.get(jid, ""), "DECISIONS")
        if found:
            lines += [found, ""]
    return "\n".join(lines).rstrip() + "\n"


def pr_body(st: dict, job_id: str, mj: dict, report: str | None) -> str:
    return "\n".join([
        f"Job `{job_id}` of the Arkimedes plan `{st['slug']}` (run file `runs/{st['repo']}/{st['slug']}.md`).",
        "",
        f"**Includes:** {mj['includes']}",
        f"**Excludes:** {mj['excludes']}",
        "",
        "**Done when:**",
        _bullets(mj["done"]),
        "",
        "## Worker report",
        "",
        (report or "(no report)").strip(),
        "",
        "Merged by `ark-merge` only after the job gate, a CLEAN review and the integration gate passed for this head.",
    ]) + "\n"


def final_pr_body(st: dict) -> str:
    rows = [f"| {jid} | {j['status']} | {j['pr']['url'] if j.get('pr') else '-'} |" for jid, j in st["jobs"].items()]
    blocked = [f"- `{jid}`: {j['reason']}" for jid, j in st["jobs"].items() if j["status"] == "blocked"]
    parts = [f"**Decisions:** `docs/decisions/{st['slug']}.md` (in this PR)", "",
             f"Arkimedes run of the plan `{st['slug']}`, approved {st['approved_at'][:10]}.", "",
             "| job | status | PR |", "|---|---|---|", *rows]
    if blocked:
        parts += ["", "**Blocked jobs** (not in this PR):", *blocked]
    return "\n".join(parts) + "\n"


def worker_status(result: str) -> tuple[str, str]:
    found = STATUS.findall(result or "")
    if not found:
        return "FAILED", "no STATUS line in the final message"
    status, detail = found[-1]
    return status, detail.strip()


def parse_json_line(text: str) -> dict | None:
    """The last line of `text` that is a JSON object (backticks around it are ignored)."""
    for line in reversed((text or "").splitlines()):
        line = line.strip().strip("`").strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                return d
    return None
```

`agents/ark-dev.md`:

```markdown
You are an Arkimedes worker: a developer running headless, alone, on one job of an approved plan.

- Your brief is the whole plan for you. Do exactly that job: its tasks, inside its area. Nothing more.
- You work in a git worktree on your own branch. Commit there. Never push, never open or merge a PR,
  never touch other branches: the runner does all of that after you finish.
- Hooks enforce the rules: writes outside the area, reading the planning folders, pushing and merging
  are denied. A denial means: change course, or end BLOCKED with the question. Do not work around it.
- Test first for new behaviour. Run the job's gate yourself; when you try to stop with the gate red,
  a hook sends you back with its output.
- Nobody will answer you during the run. When the brief does not cover a decision, pick the simplest
  option that fits the plan, note it under DECISIONS, and go on. End BLOCKED only when every option
  would break the plan or the area.
- Your final message is short, and its last line is the STATUS line the brief asks for.
```

`agents/ark-reviewer.md`:

```markdown
You are an Arkimedes reviewer: you read one job's diff against its brief and give a verdict.

- You do not edit files, run formatters, commit, push or comment on PRs. Read and run read-only
  commands (git diff, git log, the tests) only.
- Judge the diff against the job: its tasks, Done-when items, Includes and Excludes. Scope creep is a
  finding, like a bug is.
- Be concrete: each finding names the file and line and says what to change.
- Your final message ends with the one JSON line the brief asks for, and nothing after it.
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_brief.py`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/brief.py agents/ark-dev.md agents/ark-reviewer.md tests/test_brief.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): worker and reviewer briefs, tick prompts, default roles"
```

---

### Task 8: Claude command lines and worker settings

**Files:**
- Create: `lib/ark/claude.py`, `worker-settings.json`, `tests/test_claude.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_claude.py`:

```python
import json
from pathlib import Path

from ark import claude

CODE = Path(__file__).resolve().parents[1]


def test_worker_command(monkeypatch):
    monkeypatch.setenv("ARK_CLAUDE", "/x/claude")
    cmd = claude.agent_cmd("worker", "sonnet", "do it", session="u1", usd=2)
    assert cmd[:3] == ["/x/claude", "-p", "do it"]
    for pair in (["--model", "sonnet"], ["--session-id", "u1"], ["--max-budget-usd", "2"],
                 ["--output-format", "stream-json"], ["--permission-mode", "auto"]):
        i = cmd.index(pair[0])
        assert cmd[i:i + 2] == pair
    assert "--verbose" in cmd
    assert cmd[cmd.index("--settings") + 1] == str(CODE / "worker-settings.json")
    assert cmd[cmd.index("--append-system-prompt-file") + 1] == str(CODE / "agents/ark-dev.md")


def test_resume_and_tick_commands(monkeypatch):
    monkeypatch.setenv("ARK_CLAUDE", "/x/claude")
    r = claude.agent_cmd("worker", "opus", "fix", session="u1", resume=True, usd=3)
    assert r[r.index("--resume") + 1] == "u1" and "--session-id" not in r
    t = claude.agent_cmd("tick", "opus", "judge", session="u2", usd=1)
    assert t[t.index("--output-format") + 1] == "json"
    assert t[t.index("--disallowedTools") + 1] == "Bash,Write,Edit,MultiEdit,NotebookEdit"
    assert "--append-system-prompt-file" not in t
    v = claude.agent_cmd("reviewer", "sonnet", "review", session="u3", usd=1)
    assert v[v.index("--append-system-prompt-file") + 1].endswith("agents/ark-reviewer.md")


def test_runner_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("CLAUDECODE", "1")
    env = claude.runner_env("worker", area='["src/**"]', job="shell")
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env
    assert env["ARK_ROLE"] == "worker" and env["ARKIMEDES"] == "1"
    assert env["ARK_AREA"] == '["src/**"]' and env["ARK_JOB"] == "shell"


def test_tiers():
    assert [claude.MODELS[t] for t in ("T0", "T1", "T2")] == ["haiku", "sonnet", "opus"]
    assert claude.NEXT_TIER == {"T0": "T1", "T1": "T2"}
    assert claude.TIER_MINUTES == {"T0": 20, "T1": 60, "T2": 120}


def test_worker_settings_register_the_guards():
    s = json.loads((CODE / "worker-settings.json").read_text())
    commands = [h["command"] for groups in s["hooks"].values() for g in groups for h in g["hooks"]]
    for name in ("area-guard.py", "read-guard.py", "merge-guard.py", "done-gate.sh"):
        assert any(c.endswith(f"/hooks/{name}") or c.endswith(f"/hooks/{name}\"") for c in commands), name
    assert "Bash(git push:*)" in s["permissions"]["deny"]
    assert s["env"]["ARKIMEDES"] == "1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_claude.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'ark.claude'`.

- [ ] **Step 3: Implement** — `lib/ark/claude.py`:

```python
"""Command lines and environments for every `claude -p` the runner starts.

Hard caps (CLI 2.1.318 has no --max-turns): --max-budget-usd per process, and the wall-clock
deadline per tier that ark-loop enforces by killing the process group."""
from __future__ import annotations

import os
import shutil

from . import paths

MODELS = {"T0": "haiku", "T1": "sonnet", "T2": "opus"}
TIER_MINUTES = {"T0": 20, "T1": 60, "T2": 120}
NEXT_TIER = {"T0": "T1", "T1": "T2"}
TICK_MODEL = {"judge": "opus", "decisions": "sonnet"}
ROLE_FILES = {"worker": "ark-dev.md", "reviewer": "ark-reviewer.md"}
NO_TOOLS = "Bash,Write,Edit,MultiEdit,NotebookEdit"


def claude_bin() -> str:
    return os.environ.get("ARK_CLAUDE") or shutil.which("claude") or "claude"


def agent_cmd(role: str, model: str, prompt: str, *, session: str, resume: bool = False, usd: float) -> list[str]:
    code = paths.code_dir()
    cmd = [claude_bin(), "-p", prompt, "--model", model,
           "--settings", str(code / "worker-settings.json"),
           "--permission-mode", "auto",
           "--max-budget-usd", f"{usd:g}"]
    if role in ROLE_FILES:
        cmd += ["--append-system-prompt-file", str(code / "agents" / ROLE_FILES[role]),
                "--output-format", "stream-json", "--verbose"]
    else:
        cmd += ["--output-format", "json", "--disallowedTools", NO_TOOLS]
    cmd += ["--resume", session] if resume else ["--session-id", session]
    return cmd


def runner_env(role: str, **extra: str) -> dict:
    """The environment of a runner process: never the API key (it would bill per token), never
    the parent's Claude Code session markers; ARK_ROLE switches the guards on."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDECODE")}
    env["ARK_ROLE"] = role
    env["ARKIMEDES"] = "1"
    env["ARK_HOME"] = str(paths.ark_home())
    for k, v in extra.items():
        env[f"ARK_{k.upper()}"] = str(v)
    return env
```

`worker-settings.json` (hook commands use absolute paths, like `settings.json`):

```json
{
  "env": { "ARKIMEDES": "1" },
  "permissions": {
    "allow": [
      "Read", "Edit", "Write", "MultiEdit", "Grep", "Glob",
      "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
      "Bash(git add:*)", "Bash(git commit:*)", "Bash(git fetch:*)", "Bash(git merge:*)",
      "Bash(git restore:*)", "Bash(git rm:*)", "Bash(git mv:*)"
    ],
    "deny": [
      "Bash(git push:*)", "Bash(gh pr merge:*)", "Bash(gh pr create:*)",
      "Bash(gh pr review:*)", "Bash(gh api:*)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      { "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "python3 \"/Users/marcozanger/.claude/arkimedes/hooks/area-guard.py\"" }] },
      { "matcher": "Read|Grep|Glob|Bash",
        "hooks": [{ "type": "command", "command": "python3 \"/Users/marcozanger/.claude/arkimedes/hooks/read-guard.py\"" }] },
      { "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "python3 \"/Users/marcozanger/.claude/arkimedes/hooks/merge-guard.py\"" }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "bash \"/Users/marcozanger/.claude/arkimedes/hooks/done-gate.sh\"" }] }
    ]
  }
}
```

(The `--settings` file adds to Marco's user settings, so `desk-guard.py` and `require-worktree.sh` also apply to every runner process. The worktree is a linked worktree, which `require-worktree.sh` allows.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_claude.py`
Expected: 4 pass, `test_worker_settings_register_the_guards` passes too (it only reads the JSON; the hook files come in Task 9).

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/claude.py worker-settings.json tests/test_claude.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): claude command lines per role, worker settings"
```

---

### Task 9: Worker guards

**Files:**
- Create: `hooks/area-guard.py`, `hooks/read-guard.py`, `hooks/merge-guard.py`, `hooks/done-gate.sh`, `tests/test_worker_guards.py`
- Modify: `hooks/session-start.sh`

All three Python guards do nothing when `ARK_ROLE` is not set (Marco's sessions), and fail **closed** for runner roles: an input they cannot check is denied. `done-gate.sh` only acts for `ARK_ROLE=worker`.

- [ ] **Step 1: Write the failing tests** — `tests/test_worker_guards.py`:

```python
import json
import os
import subprocess
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def hook(name, payload, **env_over):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARK_")}
    env.update(env_over)
    cmd = ["bash", str(CODE / "hooks" / name)] if name.endswith(".sh") else ["python3", str(CODE / "hooks" / name)]
    r = subprocess.run(cmd, input=payload if isinstance(payload, str) else json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def decision(out):
    return out["hookSpecificOutput"]["permissionDecision"] if out else "allow"


def worker_env(wt, report, **over):
    return {"ARK_ROLE": "worker", "ARK_WORKTREE": str(wt), "ARK_AREA": json.dumps(["src/shell/**"]),
            "ARK_REPORT": str(report), **over}


def test_area_guard(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    report = tmp_path / "runs" / "shell.report.md"
    env = worker_env(wt, report)
    w = lambda p, **e: decision(hook("area-guard.py", {"tool_name": "Write", "tool_input": {"file_path": p},
                                                       "cwd": str(wt)}, **{**env, **e}))
    assert w(str(wt / "src/shell/a.py")) == "allow"
    assert w("src/shell/b.py") == "allow"
    assert w(str(wt / "src/ui/a.py")) == "deny"
    assert w(str(wt / "src/shell/../ui/a.py")) == "deny"
    assert w("/tmp/elsewhere.py") == "deny"
    assert w(str(report)) == "allow"
    assert w(str(wt / "src/shell/a.py"), ARK_ROLE="reviewer") == "deny"
    assert w(str(wt / "src/ui/a.py"), ARK_ROLE="") == "allow"          # Marco's sessions: untouched
    assert decision(hook("area-guard.py", {"tool_name": "Read", "tool_input": {"file_path": "/x"}}, **env)) == "allow"


def test_area_guard_fails_closed_for_workers(tmp_path):
    env = worker_env(tmp_path, tmp_path / "r.md")
    for payload in ["", "not json", {"tool_name": "Edit", "tool_input": {}},
                    {"tool_name": "Edit", "tool_input": {"file_path": 5}}]:
        assert decision(hook("area-guard.py", payload, **env)) == "deny", payload
    assert decision(hook("area-guard.py", "not json")) == "allow"       # no role: never blocks


def test_read_guard(tmp_path):
    home = tmp_path / "ark"
    env = {"ARK_ROLE": "worker", "ARK_HOME": str(home)}
    r = lambda tool, inp, **e: decision(hook("read-guard.py", {"tool_name": tool, "tool_input": inp,
                                                               "cwd": str(tmp_path)}, **{**env, **e}))
    assert r("Read", {"file_path": str(home / "desk/demo/x/plan.md")}) == "deny"
    assert r("Grep", {"pattern": "x", "path": str(home / "approvals")}) == "deny"
    assert r("Glob", {"pattern": "**/*.md", "path": str(home / "desk")}) == "deny"
    assert r("Bash", {"command": f"cat {home}/desk/demo/x/plan.md"}) == "deny"
    assert r("Bash", {"command": "cat ~/.claude/arkimedes/desk/demo/x/plan.md"}) == "deny"
    assert r("Read", {"file_path": str(home / "runs/demo/x/shell.report.md")}) == "allow"
    assert r("Read", {"file_path": str(tmp_path / "src/a.py")}) == "allow"
    assert r("Read", {"file_path": str(home / "desk/demo/x/plan.md")}, ARK_ROLE="") == "allow"
    assert r("Read", {"file_path": str(home / "desk/demo/x/plan.md")}, ARK_ROLE="tick") == "allow"


def test_merge_guard():
    m = lambda c, role="worker": decision(hook("merge-guard.py", {"tool_name": "Bash", "tool_input": {"command": c}},
                                               **({"ARK_ROLE": role} if role else {})))
    for c in ["gh pr merge 3 --squash", "gh pr review 3 --approve", "gh pr create --fill", "gh api repos/x/pulls",
              "git push origin HEAD", "git -C /wt push", "cd /wt && git push -f"]:
        assert m(c) == "deny", c
        assert m(c, role="tick") == "deny", c
    for c in ["git status", "gh pr view 3", "git commit -m 'push the button'", "git log --oneline"]:
        assert m(c) == "allow", c
    assert m("gh pr merge 3", role=None) == "allow"        # Marco's sessions: untouched


def test_done_gate(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    rd = tmp_path / "run"
    rd.mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"text": "working"}\n')
    env = {"ARK_ROLE": "worker", "ARK_WORKTREE": str(wt), "ARK_RUN_DIR": str(rd), "ARK_JOB": "shell",
           "ARK_GATE": "test -f ok || (echo gate is red; exit 1)"}
    stop = lambda **e: hook("done-gate.sh", {"transcript_path": str(transcript)}, **{**env, **e})
    for n in range(3):
        out = stop()
        assert out["decision"] == "block" and "gate is red" in out["reason"]
    assert stop() is None                                   # 3 blocks: it may end FAILED
    (rd / "shell.doneblocks").unlink()
    transcript.write_text('{"text": "STATUS: FAILED — cannot"}\n')
    assert stop() is None                                   # it already says FAILED
    transcript.write_text('{"text": "done"}\n')
    (wt / "ok").write_text("")
    assert stop() is None                                   # green
    assert stop(ARK_ROLE="reviewer") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_worker_guards.py`
Expected: FAIL — the hook files do not exist (`python3: can't open file`).

- [ ] **Step 3: Implement**

`hooks/area-guard.py`:

```python
#!/usr/bin/env python3
"""PreToolUse (Write|Edit|MultiEdit|NotebookEdit) for runner processes.

Workers may write only inside the job's area (ARK_AREA, globs relative to ARK_WORKTREE) and their
report (ARK_REPORT); reviewers write nothing. Fails closed for those roles: what it cannot check
is denied. Without ARK_ROLE (Marco's sessions) it does nothing."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def check(raw: str) -> None:
    role = os.environ.get("ARK_ROLE")
    data = json.loads(raw)
    tool = data.get("tool_name")
    if tool not in WRITE_TOOLS:
        return
    if role == "reviewer":
        deny("Reviewers do not edit files. Put the change in a finding.")
    ti = data.get("tool_input") or {}
    p = ti.get("file_path") or ti.get("notebook_path")
    if not isinstance(p, str) or not p:
        deny("area-guard: no file path in this call.")
    wt = Path(os.path.realpath(os.environ["ARK_WORKTREE"]))
    target = Path(os.path.realpath(os.path.join(data.get("cwd") or wt, os.path.expanduser(p))))
    report = os.environ.get("ARK_REPORT")
    if report and target == Path(os.path.realpath(report)):
        return
    try:
        rel = target.relative_to(wt).as_posix()
    except ValueError:
        deny(f"{target} is outside your worktree {wt}. Work only there.")
    from ark.globs import match_any
    area = json.loads(os.environ["ARK_AREA"])
    if not match_any(rel, area):
        deny(f"{rel} is outside this job's area {area}. Do not change it: note it under Open issues "
             "in your report and, if the job cannot be done without it, end with STATUS: BLOCKED.")


def main() -> None:
    if os.environ.get("ARK_ROLE") not in ("worker", "reviewer"):
        return
    try:
        check(sys.stdin.read())
    except SystemExit:
        raise
    except Exception as e:  # fail closed for workers and reviewers
        deny(f"area-guard could not check this call ({type(e).__name__}); it is denied.")


if __name__ == "__main__":
    main()
```

`hooks/read-guard.py`:

```python
#!/usr/bin/env python3
"""PreToolUse (Read|Grep|Glob|Bash) for workers and reviewers: no reading plans or approvals.

The brief is the whole plan a worker gets (§6). Fails closed for those roles; nothing for others."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

REASON = ("Workers and reviewers do not read plans or approvals: your brief is the whole plan. "
          "If something is missing from it, say so in your report.")


def deny(reason: str = REASON) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def check(raw: str) -> None:
    from ark import paths
    home = Path(os.path.realpath(paths.ark_home()))
    closed = [home / "desk", home / "approvals"]
    data = json.loads(raw)
    tool, ti = data.get("tool_name"), data.get("tool_input") or {}
    if tool == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            deny("read-guard: no command.")
        names = [str(c) for c in closed] + ["arkimedes/desk", "arkimedes/approvals"]
        if any(n in cmd for n in names):
            deny()
        return
    if tool not in ("Read", "Grep", "Glob"):
        return
    p = ti.get("file_path") or ti.get("path")
    if p is None:
        return
    if not isinstance(p, str):
        deny("read-guard: bad path.")
    target = Path(os.path.realpath(os.path.join(data.get("cwd") or os.getcwd(), os.path.expanduser(p))))
    if any(target == c or c in target.parents for c in closed):
        deny()


def main() -> None:
    if os.environ.get("ARK_ROLE") not in ("worker", "reviewer"):
        return
    try:
        check(sys.stdin.read())
    except SystemExit:
        raise
    except Exception as e:
        deny(f"read-guard could not check this call ({type(e).__name__}); it is denied.")


if __name__ == "__main__":
    main()
```

`hooks/merge-guard.py`:

```python
#!/usr/bin/env python3
"""PreToolUse (Bash) for every runner process (ARK_ROLE set): no merging, reviewing, creating or
editing PRs, no `gh api`, no `git push`. Only ark-loop does those, in code, after ark-merge's
checks (§7). Fails closed for runner roles; nothing for Marco's sessions."""
import json
import os
import re
import sys

FORBIDDEN = [
    (re.compile(r"\bgh\s+pr\s+(merge|review|create|edit|close|ready|comment)\b"), "PRs are the runner's job"),
    (re.compile(r"\bgh\s+api\b"), "gh api is not available to runner processes"),
    (re.compile(r"\bgit\b(\s+-[Cc]\s+\S+|\s+--?[\w-]+(=\S+)?)*\s+push\b"), "pushing is the runner's job"),
]


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason + ". Commit and finish; "
                                             "the runner pushes, opens and merges the PR."}}))
    sys.exit(0)


def main() -> None:
    if not os.environ.get("ARK_ROLE"):
        return
    try:
        data = json.loads(sys.stdin.read())
        if data.get("tool_name") != "Bash":
            return
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str):
            deny("merge-guard: no command")
        for rx, why in FORBIDDEN:
            if rx.search(cmd):
                deny(why)
    except SystemExit:
        raise
    except Exception as e:
        deny(f"merge-guard could not check this call ({type(e).__name__})")


if __name__ == "__main__":
    main()
```

(`git commit -m 'push the button'` passes: the push regex needs `push` as the git subcommand, after only options.)

`hooks/done-gate.sh`:

```bash
#!/bin/bash
# Stop hook for runner workers (§6): while the job gate is red the worker may not stop; the gate
# output goes back to it. After 3 blocks, or when its last message already says FAILED/BLOCKED,
# it may end. Does nothing outside ARK_ROLE=worker.
[ "$ARK_ROLE" = "worker" ] || exit 0
[ -n "$ARK_GATE" ] && [ -d "$ARK_WORKTREE" ] && [ -n "$ARK_RUN_DIR" ] || exit 0
input=$(cat)
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)
if [ -n "$transcript" ] && [ -f "$transcript" ] &&
   tail -c 20000 "$transcript" | grep -Eq 'STATUS: (FAILED|BLOCKED)'; then
  exit 0
fi
count_file="$ARK_RUN_DIR/$ARK_JOB.doneblocks"
n=$(cat "$count_file" 2>/dev/null || echo 0)
[ "$n" -ge 3 ] && exit 0
out=$(cd "$ARK_WORKTREE" && sh -c "$ARK_GATE" 2>&1) && exit 0
echo $((n + 1)) > "$count_file"
tail=$(printf '%s' "$out" | tail -c 3000)
jq -n --arg r "The job gate is red ($((n + 1))/3). Fix it before you finish, or end with STATUS: FAILED — <why>.
Gate: $ARK_GATE
Output (tail):
$tail" '{decision: "block", reason: $r}'
```

`hooks/session-start.sh` — add right after the shebang and comments, before anything else:

```bash
# Runner processes (workers, reviewers, ticks) get no session context: their brief is all they need (§10).
[ -n "$ARK_ROLE" ] && exit 0
```

Make the new hooks executable:

```bash
chmod +x ~/.claude/arkimedes/hooks/area-guard.py ~/.claude/arkimedes/hooks/read-guard.py \
         ~/.claude/arkimedes/hooks/merge-guard.py ~/.claude/arkimedes/hooks/done-gate.sh
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_worker_guards.py tests/test_claude.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add hooks/area-guard.py hooks/read-guard.py hooks/merge-guard.py hooks/done-gate.sh \
    hooks/session-start.sh tests/test_worker_guards.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): worker guards (area, read, merge, done-gate)"
```

---

### Task 10: ark-merge and ark-integrate

**Files:**
- Create: `lib/ark/merge.py`, `lib/ark/integrate.py`, `tests/test_merge.py`

`merge.check` is the §7 list, in code. The loop calls `merge.merge` in-process; `bin/ark-merge` (Task 13) is the same function from a terminal. The gate, verdict and integration results it checks are records the runner writes from process exit codes and parsed output (`<job>.gate.json`, `<job>.verdict.json`, `<job>.integrate.json`), each tied to a head sha.

- [ ] **Step 1: Write the failing tests** — `tests/test_merge.py`:

```python
import json
import subprocess

import pytest
from helpers import approve, manifest, plan_text

from ark import forge, gitops, integrate, merge, paths, run
from ark.plan import load_plan

SLUG = "notes-edit"


def commit_on(repo, tmp_path, branch, start, rel, text="x\n"):
    wt = tmp_path / "w" / branch.replace("/", "-")
    gitops.add_worktree(repo, wt, branch, start)
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text(text)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", rel], check=True)
    gitops.push_branch(wt, branch)
    return gitops.rev(wt, "HEAD")


@pytest.fixture
def ready(runner, make_feature, tmp_path):
    """A run whose job `shell` has a PR that passes every check."""
    def make(m=None):
        repo = runner()
        plan = make_feature(plan_text(m), create_repo=False)
        st = run.new_state(json.loads(approve(plan).read_text()))
        st.update(status="running", default_branch="master")
        for mj in load_plan(plan).manifest["jobs"]:
            st["jobs"][mj["id"]] = run.new_job(mj)
        base = gitops.rev(repo, "HEAD")
        gitops.push_ref(repo, base, f"ark/{SLUG}")
        head = commit_on(repo, tmp_path, f"ark/{SLUG}-shell", base, "src/shell/a.txt")
        f = forge.LocalForge()
        pr = f.create(repo, base=f"ark/{SLUG}", head=f"ark/{SLUG}-shell", title="feat: shell", body="")
        st["jobs"]["shell"].update(head=head, pr=pr)
        run.save(st)
        d = paths.run_dir("demo", SLUG)
        merge.record(d, "shell", "gate", {"ok": True, "head": head})
        merge.record(d, "shell", "verdict", {"verdict": "CLEAN", "head": head, "findings": []})
        merge.record(d, "shell", "integrate", {"ok": True, "base": base, "head": head})
        return st, f, repo, d, plan, base
    return make


def test_all_checks_hold_and_merge(ready):
    st, f, repo, d, plan, base = ready()
    assert merge.check(st, "shell", f, repo) is None
    merge.merge(st, "shell", f, repo)
    gitops.fetch(repo)
    assert gitops.changed_files(repo, base, f"origin/ark/{SLUG}") == ["src/shell/a.txt"]


def test_refusals(ready, tmp_path, monkeypatch):
    st, f, repo, d, plan, base = ready()
    j = st["jobs"]["shell"]

    def refused(fragment):
        reason = merge.check(st, "shell", f, repo)
        assert reason and fragment in reason, reason
        with pytest.raises(merge.MergeRefused):
            merge.merge(st, "shell", f, repo)

    monkeypatch.setenv("ARK_ROLE", "worker")
    refused("never merge")
    monkeypatch.delenv("ARK_ROLE")

    d_gate = merge.read_record(d, "shell", "gate")
    merge.record(d, "shell", "gate", {"ok": True, "head": "0" * 40})
    refused("gate")
    merge.record(d, "shell", "gate", d_gate)

    merge.record(d, "shell", "verdict", {"verdict": "FIX", "head": j["head"], "findings": ["x"]})
    refused("CLEAN")
    (d / "shell.verdict.json").unlink()
    refused("CLEAN")
    merge.record(d, "shell", "verdict", {"verdict": "CLEAN", "head": j["head"], "findings": []})

    pr = j["pr"]
    j["pr"] = f.create(repo, base="master", head=f"ark/{SLUG}-shell", title="t", body="")
    refused("base")
    j["pr"] = f.create(repo, base=f"ark/{SLUG}", head=f"ark/{SLUG}-ui", title="t", body="")
    refused("head branch")
    j["pr"] = pr

    assert merge.check(st, "ghost", f, repo) == "job ghost is not in the approved manifest"

    gitops.commit_file(repo, f"ark/{SLUG}", "other.txt", "o\n", "moved", tmp_path / "scratch")
    refused("integration")
    gitops.fetch(repo)
    merge.record(d, "shell", "integrate", {"ok": True, "base": gitops.rev(repo, f"origin/ark/{SLUG}"),
                                           "head": j["head"]})
    assert merge.check(st, "shell", f, repo) is None

    plan.write_text(plan.read_text() + "\nedited\n")
    refused("plan changed")


def test_refuses_files_outside_the_area_and_a_moved_head(ready, tmp_path):
    st, f, repo, d, plan, base = ready()
    j = st["jobs"]["shell"]
    wt = tmp_path / "w" / f"ark-{SLUG}-shell"
    (wt / "README.md").write_text("changed\n")
    subprocess.run(["git", "-C", str(wt), "commit", "-qam", "outside"], check=True)
    gitops.push_branch(wt, f"ark/{SLUG}-shell")
    assert "moved" in merge.check(st, "shell", f, repo)
    j["head"] = gitops.rev(wt, "HEAD")
    assert "outside the job's area: README.md" in merge.check(st, "shell", f, repo)


def test_integrate_green_and_red(ready):
    st, f, repo, d, plan, base = ready()
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert out["ok"] and out["base"] == base and out["head"] == st["jobs"]["shell"]["head"]
    assert not list((paths.worktrees_dir() / "demo" / SLUG).glob("_integrate-*"))


def test_integrate_red(ready):
    st, f, repo, d, plan, base = ready(manifest(integration_gate="echo boom; exit 1"))
    out = integrate.integrate("demo", SLUG, "shell", st["jobs"]["shell"]["head"])
    assert not out["ok"] and "integration gate failed" in out["reason"] and "boom" in out["tail"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_merge.py`
Expected: FAIL — `ImportError: cannot import name 'integrate' from 'ark'`.

- [ ] **Step 3: Implement**

`lib/ark/merge.py`:

```python
"""ark-merge: the only way a job PR reaches ark/<slug> (§7). Every check must hold.

The loop calls merge() in-process and records the result in the run state; `ark-merge` runs the
same function from a terminal (it does not change the run state)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import forge as forge_mod, gitops, paths, run
from .globs import match_any
from .plan import load_plan, sha256_file


class MergeRefused(Exception):
    pass


def record(run_dir, job: str, kind: str, data: dict) -> None:
    """A gate / verdict / integrate result, written by the runner (never by a model)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / f"{job}.{kind}.json.tmp"
    tmp.write_text(json.dumps({**data, "at": run.now()}, indent=2), encoding="utf-8")
    tmp.replace(run_dir / f"{job}.{kind}.json")


def read_record(run_dir, job: str, kind: str) -> dict | None:
    try:
        return json.loads((Path(run_dir) / f"{job}.{kind}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def manifest_jobs(st: dict) -> dict:
    return {j["id"]: j for j in load_plan(Path(st["plan"])).manifest["jobs"]}


def check(st: dict, job: str, forge, repo) -> str | None:
    """None when the job's PR may be merged into ark/<slug>; otherwise the first reason it may not."""
    if os.environ.get("ARK_ROLE") in ("worker", "reviewer"):
        return "workers and reviewers never merge"
    slug = st["slug"]
    base = f"ark/{slug}"
    d = paths.run_dir(st["repo"], slug)
    try:
        approved = json.loads(paths.approval_file(st["repo"], slug).read_text(encoding="utf-8"))["sha256"]
        sha = sha256_file(st["plan"])
    except (OSError, ValueError, KeyError):
        return "no readable approval or plan"
    if sha != approved or sha != st["plan_sha"]:
        return "the plan changed after its approval"
    mjobs = manifest_jobs(st)
    if job not in mjobs or job not in st["jobs"]:
        return f"job {job} is not in the approved manifest"
    j = st["jobs"][job]
    if not j.get("pr") or not j.get("head"):
        return "the job has no PR or head yet"
    v = forge.view(repo, j["pr"]["number"])
    if v["state"] != "OPEN":
        return f"the PR is {v['state']}"
    if v["base"] != base:
        return f"the PR base is {v['base']}, not {base}"
    if v["head"] != f"{base}-{job}":
        return f"the PR head branch is {v['head']}, not {base}-{job}"
    if v["head_sha"] != j["head"]:
        return "the PR head moved after it was gated and reviewed"
    gitops.fetch(repo)
    outside = [f for f in gitops.changed_files(repo, f"origin/{base}", j["head"]) if not match_any(f, mjobs[job]["area"])]
    if outside:
        return "files outside the job's area: " + ", ".join(outside[:5])
    g = read_record(d, job, "gate")
    if not g or not g.get("ok") or g.get("head") != j["head"]:
        return "no green job gate for this head"
    r = read_record(d, job, "verdict")
    if not r or r.get("verdict") != "CLEAN" or r.get("head") != j["head"]:
        return "no CLEAN review verdict for this head"
    i = read_record(d, job, "integrate")
    if not i or not i.get("ok") or i.get("head") != j["head"] or i.get("base") != gitops.rev(repo, f"origin/{base}"):
        return f"no green integration gate for this head on the current {base}"
    return None


def merge(st: dict, job: str, forge, repo) -> None:
    reason = check(st, job, forge, repo)
    if reason:
        raise MergeRefused(reason)
    j = st["jobs"][job]
    forge.merge(repo, j["pr"]["number"], subject=manifest_jobs(st)[job]["pr_title"], head_sha=j["head"])


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or "/" not in argv[0]:
        print("uso: ark-merge <repo>/<slug> <job>", file=sys.stderr)
        return 2
    repo_name, _, slug = argv[0].rpartition("/")
    st, repo = run.load(repo_name, slug), paths.find_repo(repo_name)
    if st is None or repo is None:
        print("FAIL no run or no repo")
        return 1
    try:
        merge(st, argv[1], forge_mod.get(), repo)
    except (MergeRefused, forge_mod.ForgeError, gitops.GitError) as e:
        print(f"FAIL {e}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`lib/ark/integrate.py`:

```python
"""ark-integrate (§5): squash a job head onto the current ark/<slug> in a scratch worktree and run
the plan's setup and integration_gate there. Prints one JSON line {ok, base, head, reason, tail};
the loop records it, and ark-merge only merges onto the exact base it was green for."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import gitops, paths, run
from .plan import load_plan


def integrate(repo_name: str, slug: str, job: str, head: str) -> dict:
    st = run.load(repo_name, slug)
    repo = paths.find_repo(repo_name)
    m = load_plan(Path(st["plan"])).manifest
    gitops.fetch(repo)
    base = gitops.rev(repo, f"origin/ark/{slug}")
    out = {"ok": False, "base": base, "head": head, "reason": "", "tail": ""}
    scratch = paths.worktrees_dir() / repo_name / slug / f"_integrate-{job}"
    try:
        if gitops.squash_keep(repo, base, head, f"integration check {job}", scratch) is None:
            return {**out, "reason": f"{job} does not merge cleanly onto ark/{slug}"}
        for cmd in [*(m.get("setup") or []), m["integration_gate"]]:
            r = subprocess.run(["sh", "-c", cmd], cwd=scratch, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL)
            if r.returncode:
                what = "integration gate" if cmd == m["integration_gate"] else f"setup `{cmd}`"
                return {**out, "reason": f"{what} failed (exit {r.returncode})",
                        "tail": (r.stdout + r.stderr)[-3000:]}
        return {**out, "ok": True}
    finally:
        gitops.remove_worktree(repo, scratch)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or "/" not in argv[0]:
        print("uso: ark-integrate <repo>/<slug> <job> <head-sha>", file=sys.stderr)
        return 2
    repo_name, _, slug = argv[0].rpartition("/")
    try:
        out = integrate(repo_name, slug, argv[1], argv[2])
    except Exception as e:  # the loop reads the JSON line; never leave it without one
        out = {"ok": False, "base": None, "head": argv[2], "reason": f"{type(e).__name__}: {e}", "tail": ""}
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_merge.py`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/merge.py lib/ark/integrate.py tests/test_merge.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): ark-merge checks and the integration gate"
```

---

### Task 11: The loop — runs and the job state machine

**Files:**
- Create: `lib/ark/loop.py`, `tests/fake_claude.py`, `tests/test_loop.py`, `bin/ark-integrate`

`loop.cycle()` is one pass over every approved plan: start waiting runs, hold runs whose plan changed, consume receipts, then move every job forward as far as limits allow. It has no model. This task covers everything up to every job being `merged` or `blocked`; closing the run is Task 12.

- [ ] **Step 1: Write the fake claude** — `tests/fake_claude.py` (then `chmod +x tests/fake_claude.py`):

```python
#!/usr/bin/env python3
"""Stand-in for `claude -p` in the runner tests. It acts by ARK_ROLE:

worker    appends "attempt <n>" to <area[0] with ** -> <job>.txt> (or outside/<job>.txt when the job
          is in FAKE_OUTSIDE), commits, writes the report, ends STATUS: DONE. FAKE_FAIL=job:n,...
          makes attempt n end FAILED. FAKE_SLEEP=seconds delays it. FAKE_STATE/max_active records
          the most workers alive at once.
reviewer  FIX on the first review of a job in FAKE_FIX, CLEAN otherwise.
tick      the decisions file when asked for one, else a judge answer `retry`.
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]


def opt(name):
    return args[args.index(name) + 1] if name in args else None


PROMPT = opt("-p") or ""
SESSION = opt("--session-id") or opt("--resume")
ROLE = os.environ.get("ARK_ROLE")
STATE = Path(os.environ["FAKE_STATE"])


def bump(name):
    f = STATE / f"{name}.count"
    n = int(f.read_text()) + 1 if f.exists() else 1
    f.write_text(str(n))
    return n


def track(delta):
    with open(STATE / "active.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        f, m = STATE / "active", STATE / "max_active"
        n = (int(f.read_text()) if f.exists() else 0) + delta
        f.write_text(str(n))
        if n > (int(m.read_text()) if m.exists() else 0):
            m.write_text(str(n))


def listed(var, job):
    return job in os.environ.get(var, "").split(",")


def emit(result, stream=True):
    if stream:
        print(json.dumps({"type": "system", "subtype": "init", "session_id": SESSION}))
    print(json.dumps({"type": "result", "result": result, "session_id": SESSION,
                      "total_cost_usd": 0.01, "is_error": False}))


if ROLE == "worker":
    job, wt = os.environ["ARK_JOB"], Path(os.environ["ARK_WORKTREE"])
    track(1)
    try:
        n = bump(f"{job}.worker")
        time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
        if f"{job}:{n}" in os.environ.get("FAKE_FAIL", "").split(","):
            emit("tests are red\nSTATUS: FAILED — tests are red")
            sys.exit(0)
        area = json.loads(os.environ["ARK_AREA"])
        rel = f"outside/{job}.txt" if listed("FAKE_OUTSIDE", job) else area[0].replace("**", f"{job}.txt")
        f = wt / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a") as h:
            h.write(f"attempt {n}\n")
        subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"{job} attempt {n}"], cwd=wt, check=True)
        Path(os.environ["ARK_REPORT"]).write_text(
            f"## What I did\n\n{job} attempt {n}.\n\n## Tests\n\nnone\n\n## DECISIONS\n\n"
            f"- Wrote {rel}. Why: the fake always does. Rejected: nothing.\n\n## Open issues\n\nnone\n")
        emit("done\nSTATUS: DONE")
    finally:
        track(-1)
elif ROLE == "reviewer":
    job = os.environ["ARK_JOB"]
    if listed("FAKE_FIX", job) and bump(f"{job}.review") == 1:
        emit('one problem\n{"verdict": "FIX", "findings": ["src/x:1 — fix it"]}')
    else:
        emit('{"verdict": "CLEAN", "findings": []}')
elif ROLE == "tick":
    if "Write the decisions file" in PROMPT:
        emit("# Decisions — fake\n\n## shell — merged\n\n- **D1** · a fake decision · _arkimedes_\n", stream=False)
    else:
        emit('{"action": "retry", "note": "try again"}', stream=False)
else:
    sys.exit(f"fake claude: unknown ARK_ROLE {ROLE!r}")
```

- [ ] **Step 2: Write the failing tests** — `tests/test_loop.py`:

```python
import json
import time

import pytest
from helpers import approve, manifest, plan_text

from ark import forge, gitops, loop, paths, proc, run

SLUG = "notes-edit"


@pytest.fixture
def feature(runner, make_feature):
    """Seed a repo, write the plan on the desk and approve it."""
    def make(m=None, repo="demo", slug=SLUG, tasks=("T1", "T2", "T3")):
        r = runner(repo)
        p = make_feature(plan_text(m, tasks=tasks, repo=repo, slug=slug), repo=repo, slug=slug, create_repo=False)
        approve(p, repo, slug)
        return r, p
    return make


def state(repo="demo", slug=SLUG):
    return run.load(repo, slug)


def jobs_settled(repo="demo", slug=SLUG):
    st = state(repo, slug)
    return bool(st and st["jobs"]) and all(j["status"] in run.TERMINAL for j in st["jobs"].values()) \
        and not proc.active(paths.run_dir(repo, slug))


def drive(until, cycles=400):
    for _ in range(cycles):
        loop.cycle()
        if until():
            return
        time.sleep(0.05)
    raise AssertionError("not reached:\n" + "\n".join(
        f.read_text() for f in paths.runs_dir().glob("**/*.md")))


def test_jobs_merge_in_order(feature):
    repo, _ = feature()
    drive(jobs_settled)
    st = state()
    assert [j["status"] for j in st["jobs"].values()] == ["merged", "merged"]
    gitops.fetch(repo)
    files = gitops.changed_files(repo, st["base_sha"], f"origin/ark/{SLUG}")
    assert files == ["src/shell/shell.txt", "src/ui/ui.txt"]
    prs = forge.LocalForge().list(repo)
    assert [(p["base"], p["head"], p["state"]) for p in prs] == [
        (f"ark/{SLUG}", f"ark/{SLUG}-shell", "MERGED"), (f"ark/{SLUG}", f"ark/{SLUG}-ui", "MERGED")]
    log = gitops.git(repo, "log", "--format=%s", f"origin/ark/{SLUG}")
    assert log.splitlines()[:2] == ["feat: ui", "feat: shell"]
    assert gitops.git(repo, "rev-parse", "origin/master") == st["base_sha"]     # default branch untouched


def test_outside_area_blocks_the_job_and_its_dependents(feature, monkeypatch):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    repo, _ = feature()
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked" and "outside its area" in st["jobs"]["shell"]["reason"]
    assert st["jobs"]["ui"]["status"] == "blocked"
    assert gitops.remote_sha(repo, f"ark/{SLUG}-shell") is None                 # never pushed


def test_failed_attempt_is_judged_and_retried(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "shell:1")
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["attempts"] == 2 and j["tier_path"] == ["T1"]
    assert any("judge: retry" in e for e in state()["events"])


def test_review_fix_resumes_the_worker(feature, monkeypatch):
    monkeypatch.setenv("FAKE_FIX", "shell")
    feature()
    drive(jobs_settled)
    j = state()["jobs"]["shell"]
    assert j["status"] == "merged" and j["review_rounds"] == 1 and j["attempts"] == 2


def test_red_integration_gate_never_merges(feature):
    repo, _ = feature(manifest(integration_gate="exit 1"))
    drive(jobs_settled)
    st = state()
    assert st["jobs"]["shell"]["status"] == "blocked"
    assert "integration gate failed twice" in st["jobs"]["shell"]["reason"]
    assert st["jobs"]["shell"]["attempts"] == 2                                 # sent back once
    gitops.fetch(repo)
    assert gitops.rev(repo, f"origin/ark/{SLUG}") == st["base_sha"]


def test_allowed_actions():
    j = {"tier": "T1", "tier_attempts": 1}
    assert loop.allowed_actions(j) == ["block", "retry", "escalate"]
    assert loop.allowed_actions({**j, "tier_attempts": 2}) == ["block", "escalate"]
    assert loop.allowed_actions({"tier": "T2", "tier_attempts": 2}) == ["block"]


def test_existing_integration_branch_keeps_the_run_waiting(feature):
    repo, _ = feature()
    gitops.push_ref(repo, gitops.rev(repo, "HEAD"), f"ark/{SLUG}")
    loop.cycle()
    st = state()
    assert st["status"] == "waiting" and "already exists" in st["reason"]
    assert not proc.active(paths.run_dir("demo", SLUG))
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_loop.py`
Expected: FAIL — `ImportError: cannot import name 'loop' from 'ark'`.

- [ ] **Step 4: Implement** — `lib/ark/loop.py`:

```python
"""ark-loop: the always-on runner (§4, §5). No model: a state machine over files.

Each cycle: every approval → its run (runs/<repo>/<slug>/state.json); start waiting runs; hold a
run whose plan changed; consume process receipts; move every job forward within the limits.
Claude runs only as workers, reviewers and two read-only ticks (judge, decisions)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import brief, claude, forge as forge_mod, gitops, merge, paths, proc, run
from .globs import areas_overlap, match_any
from .plan import load_plan, sha256_file
from .validate import validate

STEP_ERRORS = (gitops.GitError, forge_mod.ForgeError, OSError)


def approvals() -> list[dict]:
    out = []
    for f in paths.approvals_dir().glob("**/*.json"):
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(a, dict) and {"repo", "slug", "plan", "sha256", "approved_at"} <= a.keys():
            out.append(a)
    return sorted(out, key=lambda a: a["approved_at"])


def ticks_dir() -> Path:
    """cwd of tick processes: outside every repo and outside ~/.claude/arkimedes (no project CLAUDE.md)."""
    d = paths.worktrees_dir() / "_ticks"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Ctx:
    def __init__(self, st: dict, forge):
        self.st, self.forge = st, forge
        self.plan = load_plan(Path(st["plan"]))
        self.mjobs = {j["id"]: j for j in self.plan.manifest["jobs"]}
        self.repo = paths.find_repo(st["repo"])
        self.dir = paths.run_dir(st["repo"], st["slug"])
        self.base = f"ark/{st['slug']}"

    def wt(self, job: str) -> Path:
        return paths.worktrees_dir() / self.st["repo"] / self.st["slug"] / job

    def branch(self, job: str) -> str:
        return f"{self.base}-{job}"

    def report(self, job: str) -> Path:
        return self.dir / f"{job}.report.md"

    def read_report(self, job: str) -> str | None:
        f = self.report(job)
        return f.read_text(encoding="utf-8") if f.exists() else None


# ---- runs -------------------------------------------------------------------------------------

def plan_ok(st: dict) -> bool:
    p = Path(st["plan"])
    return p.exists() and sha256_file(p) == st["plan_sha"]


def try_start(st: dict, others: list[dict]) -> bool:
    """waiting → running: the approved plan still validates, the repo is there, no running run in
    the same repo has an overlapping area, ark/<slug> does not exist yet. Then push ark/<slug>."""
    plan = Path(st["plan"])
    problems = validate(plan, approved=True)
    reason, repo, default, area = "", None, None, []
    if problems:
        reason = f"the plan does not validate: {problems[0].message}"
    else:
        repo = paths.find_repo(st["repo"])
        area = [g for j in load_plan(plan).manifest["jobs"] for g in j["area"]]
        if repo is None:
            reason = f"repo {st['repo']} not found under a workspace root"
    if not reason:
        for o in others:
            if o is not st and o["repo"] == st["repo"] and o["status"] in ("running", "held") \
                    and areas_overlap(area, o.get("area") or []):
                reason = f"its area overlaps the running plan {o['slug']}"
                break
    if not reason:
        try:
            gitops.fetch(repo)
            default = gitops.default_branch(repo)
            if gitops.remote_sha(repo, f"ark/{st['slug']}"):
                reason = f"ark/{st['slug']} already exists on origin: delete it, or approve under another slug"
        except gitops.GitError as e:
            reason = str(e)
    if reason:
        if reason != st["reason"]:
            st["reason"] = reason
            run.event(st, f"waiting: {reason}")
        return False
    base = gitops.rev(repo, f"origin/{default}")
    gitops.push_ref(repo, base, f"ark/{st['slug']}")
    st.update(status="running", reason="", default_branch=default, base_sha=base, area=area)
    for mj in load_plan(plan).manifest["jobs"]:
        st["jobs"][mj["id"]] = run.new_job(mj)
    run.event(st, f"started: ark/{st['slug']} from {default} @ {base[:8]}")
    return True


# ---- job transitions ---------------------------------------------------------------------------

def fail(c: Ctx, job: str, why: str) -> None:
    j = c.st["jobs"][job]
    j.update(status="needs_judge", failure=why, tier_attempts=j["tier_attempts"] + 1)
    run.event(c.st, f"{job} failed ({j['tier']}, attempt {j['tier_attempts']} at this tier): {why}")


def block(c: Ctx, job: str, why: str) -> None:
    c.st["jobs"][job].update(status="blocked", reason=why)
    run.alert(c.st, f"{job} blocked: {why}")


def allowed_actions(j: dict) -> list[str]:
    """block always; retry while fewer than 2 failures at this tier; escalate below T2."""
    out = ["block"]
    if j["tier_attempts"] < 2:
        out.append("retry")
    if j["tier"] in claude.NEXT_TIER:
        out.append("escalate")
    return out


def _error(c: Ctx, job: str, e: Exception) -> None:
    """A git / gh / disk error while moving a job: it stays where it is and is retried next cycle."""
    text = f"{type(e).__name__}: {e}"
    j = c.st["jobs"][job]
    if j.get("error") != text:
        j["error"] = text
        run.event(c.st, f"{job}: {text} (retrying)")


# ---- receipts ---------------------------------------------------------------------------------

def on_worker(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    if r.get("session"):
        j["session"] = r["session"]
    if r["reason"] == "stopped by Marco":
        return block(c, job, "stopped by Marco")
    if not r["reason"].startswith("setup failed"):
        j["setup_done"] = True
    if r["reason"]:
        return fail(c, job, r["reason"])
    status, detail = brief.worker_status(r["result"])
    if status != "DONE":
        return fail(c, job, f"the worker ended {status}: {detail}")
    wt = c.wt(job)
    if not gitops.is_clean(wt):
        return fail(c, job, "the worker ended DONE with uncommitted changes")
    files = gitops.changed_files(wt, f"origin/{c.base}", "HEAD")
    if not files:
        return fail(c, job, "the worker ended DONE without any change")
    outside = [f for f in files if not match_any(f, c.mjobs[job]["area"])]
    if outside:
        return block(c, job, "changed files outside its area: " + ", ".join(outside[:5]))
    gitops.push_branch(wt, c.branch(job))
    j.update(status="needs_gate", head=gitops.rev(wt, "HEAD"))
    run.event(c.st, f"{job}: worker done, head {j['head'][:8]} pushed")


def on_gate(c: Ctx, job: str, r: dict) -> None:
    ok = r["exit"] == 0 and not r["reason"]
    tail = (r["result"] or "")[-2000:]
    merge.record(c.dir, job, "gate", {"ok": ok, "head": r["meta"].get("head"), "exit": r["exit"], "tail": tail})
    if ok:
        c.st["jobs"][job]["status"] = "needs_pr"
        run.event(c.st, f"{job}: job gate green")
    else:
        fail(c, job, f"job gate red (exit {r['exit']}{', ' + r['reason'] if r['reason'] else ''}): {tail[-600:]}")


def on_review(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    d = None if r["reason"] else brief.parse_json_line(r["result"])
    verdict = (d or {}).get("verdict")
    if verdict not in ("CLEAN", "FIX"):
        j["review_errors"] += 1
        if j["review_errors"] >= 2:
            return block(c, job, "the reviewer gave no verdict twice")
        j["status"] = "needs_review"
        run.event(c.st, f"{job}: the reviewer gave no verdict; reviewing again")
        return
    findings = [str(f) for f in (d.get("findings") or [])][:20]
    merge.record(c.dir, job, "verdict", {"verdict": verdict, "head": r["meta"].get("head"), "findings": findings})
    if verdict == "CLEAN":
        j["status"] = "needs_integrate"
        run.event(c.st, f"{job}: review CLEAN")
        return
    j["review_rounds"] += 1
    run.event(c.st, f"{job}: review FIX (round {j['review_rounds']}): {len(findings)} findings")
    if j["review_rounds"] >= 3:
        return fail(c, job, "3 review rounds without CLEAN: " + "; ".join(findings)[:600])
    j["resume_msg"] = brief.resume_message("review", "\n".join(f"- {f}" for f in findings), c.st["slug"])
    j["status"] = "needs_worker"


def on_integrate(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    out = brief.parse_json_line(r["result"]) or {
        "ok": False, "base": None, "reason": r["reason"] or "ark-integrate gave no result", "tail": (r["result"] or "")[-2000:]}
    merge.record(c.dir, job, "integrate", {"ok": bool(out.get("ok")), "base": out.get("base"),
                                           "head": out.get("head") or r["meta"].get("head"),
                                           "reason": out.get("reason", ""), "tail": out.get("tail", "")})
    if out.get("ok"):
        j["status"] = "needs_merge"
        run.event(c.st, f"{job}: integration gate green on {str(out['base'])[:8]}")
        return
    j["integrate_fails"] += 1
    if j["integrate_fails"] >= 2:
        return block(c, job, f"integration gate failed twice: {out.get('reason')}")
    j["resume_msg"] = brief.resume_message("integrate", f"{out.get('reason')}\n\n{out.get('tail', '')}", c.st["slug"])
    j["status"] = "needs_worker"
    run.event(c.st, f"{job}: integration gate red ({out.get('reason')}); back to the worker")


def on_judge(c: Ctx, job: str, r: dict) -> None:
    j = c.st["jobs"][job]
    allowed = r["meta"].get("allowed") or allowed_actions(j)
    d = (None if r["reason"] else brief.parse_json_line(r["result"])) or {}
    action, note = d.get("action"), str(d.get("note") or "").strip()[:1000]
    if action not in allowed:
        action = "retry" if "retry" in allowed else "escalate" if "escalate" in allowed else "block"
        note = note or f"the judge gave no usable answer; the runner chose {action}"
    run.event(c.st, f"{job} judge: {action} — {note}")
    if action == "block":
        return block(c, job, note or j["failure"])
    if action == "escalate":
        j["tier"] = claude.NEXT_TIER[j["tier"]]
        j["tier_path"].append(j["tier"])
        j.update(tier_attempts=0, session=None, resume_msg=None, review_rounds=0)
    elif j["session"]:
        j["resume_msg"] = brief.resume_message("judge", f"{j['failure']}\n\nNote: {note}", c.st["slug"])
    j.update(note=note, status="needs_worker")


HANDLERS = {"worker": on_worker, "gate": on_gate, "review": on_review, "integrate": on_integrate, "judge": on_judge}


def consume_receipts(c: Ctx) -> None:
    proc.reap(c.dir)
    for name in proc.finished(c.dir):
        r = proc.consume(c.dir, name)
        job, _, kind = name.partition(".")
        if job == "_decisions":
            on_decisions(c, r)
            continue
        if job not in c.st["jobs"] or kind not in HANDLERS:
            continue
        c.st["jobs"][job]["usd"] = round(c.st["jobs"][job]["usd"] + (r.get("usd") or 0), 4)
        try:
            HANDLERS[kind](c, job, r)
        except STEP_ERRORS as e:  # the receipt is gone: never leave the job waiting for it
            fail(c, job, f"runner error after {kind}: {type(e).__name__}: {e}")


# ---- starting processes -----------------------------------------------------------------------

def start_worker(c: Ctx, job: str) -> None:
    j, mj, wt = c.st["jobs"][job], c.mjobs[job], c.wt(job)
    if not wt.exists():
        gitops.fetch(c.repo)
        gitops.add_worktree(c.repo, wt, c.branch(job), f"origin/{c.base}")
        j["base"] = gitops.rev(wt, "HEAD")
    pre = [] if j["setup_done"] else list(c.plan.manifest.get("setup") or [])
    (c.dir / f"{job}.doneblocks").unlink(missing_ok=True)
    report = c.report(job)
    resume = bool(j["session"] and j["resume_msg"])
    if resume:
        prompt = j["resume_msg"]
    else:
        j["session"] = str(uuid.uuid4())
        prompt = brief.worker_brief(c.st, job, mj, c.plan, worktree=wt, report=report,
                                    note=j["note"] or j["resume_msg"], previous_report=c.read_report(job))
    env = claude.runner_env("worker", worktree=wt, area=json.dumps(mj["area"]), report=report,
                            gate=mj["gate"], run_dir=c.dir, job=job)
    cmd = claude.agent_cmd("worker", claude.MODELS[j["tier"]], prompt, session=j["session"], resume=resume,
                           usd=mj["budget"]["usd"])
    proc.start(c.dir, f"{job}.worker", cmd, cwd=wt, env=env, minutes=claude.TIER_MINUTES[j["tier"]], pre=pre)
    j.update(status="working", attempts=j["attempts"] + 1, resume_msg=None, note=None, error=None)
    run.event(c.st, f"{job}: worker {j['tier']} attempt {j['attempts']}{' (resumed)' if resume else ''}")


def start_gate(c: Ctx, job: str) -> None:
    j, mj = c.st["jobs"][job], c.mjobs[job]
    proc.start(c.dir, f"{job}.gate", ["sh", "-c", mj["gate"]], cwd=c.wt(job), env=claude.runner_env("gate"),
               minutes=mj["budget"]["minutes"], meta={"head": j["head"]})
    j["status"] = "gating"


def ensure_pr(c: Ctx, job: str) -> None:
    j, mj = c.st["jobs"][job], c.mjobs[job]
    if not j["pr"]:
        j["pr"] = c.forge.create(c.repo, base=c.base, head=c.branch(job), title=mj["pr_title"],
                                 body=brief.pr_body(c.st, job, mj, c.read_report(job)))
        run.event(c.st, f"{job}: PR {j['pr']['url']}")
    j["status"] = "needs_review"


def start_review(c: Ctx, job: str) -> None:
    j, mj = c.st["jobs"][job], c.mjobs[job]
    tier = "T2" if j["tier"] == "T2" else "T1"
    prompt = brief.reviewer_brief(c.st, job, mj, c.plan, base=f"origin/{c.base}", head=j["head"])
    cmd = claude.agent_cmd("reviewer", claude.MODELS[tier], prompt, session=str(uuid.uuid4()),
                           usd=max(1, mj["budget"]["usd"] / 2))
    env = claude.runner_env("reviewer", worktree=c.wt(job), area=json.dumps(mj["area"]), job=job, run_dir=c.dir)
    proc.start(c.dir, f"{job}.review", cmd, cwd=c.wt(job), env=env, minutes=30, meta={"head": j["head"]})
    j["status"] = "reviewing"


def start_integrate(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    cmd = [str(paths.code_dir() / "bin" / "ark-integrate"), f"{c.st['repo']}/{c.st['slug']}", job, j["head"]]
    proc.start(c.dir, f"{job}.integrate", cmd, cwd=c.repo, env=claude.runner_env("integrate"),
               minutes=60, meta={"head": j["head"]})
    j["status"] = "integrating"


def do_merge(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    gitops.fetch(c.repo)
    rec = merge.read_record(c.dir, job, "integrate")
    if not rec or rec.get("base") != gitops.rev(c.repo, f"origin/{c.base}"):
        j["status"] = "needs_integrate"
        run.event(c.st, f"{job}: ark/{c.st['slug']} moved since its integration gate; checking again")
        return
    try:
        merge.merge(c.st, job, c.forge, c.repo)
    except merge.MergeRefused as e:
        return block(c, job, f"ark-merge refused: {e}")
    j["status"] = "merged"
    run.event(c.st, f"{job}: merged into ark/{c.st['slug']}")
    gitops.remove_worktree(c.repo, c.wt(job))


def start_judge(c: Ctx, job: str) -> None:
    j = c.st["jobs"][job]
    allowed = allowed_actions(j)
    if allowed == ["block"]:
        return block(c, job, f"{j['failure']} (no retry or escalation left)")
    prompt = brief.judge_prompt(c.st, job, j, c.mjobs[job], allowed, c.read_report(job))
    cmd = claude.agent_cmd("tick", claude.TICK_MODEL["judge"], prompt, session=str(uuid.uuid4()), usd=1)
    proc.start(c.dir, f"{job}.judge", cmd, cwd=ticks_dir(), env=claude.runner_env("tick"), minutes=15,
               meta={"allowed": allowed})
    j["status"] = "judging"


# ---- scheduling -------------------------------------------------------------------------------

class Slots:
    """Writers (workers alive) per repo and in total, and heavy gates, across every run."""

    def __init__(self, states, limits: dict):
        self.limits, self.writers, self.heavy = limits, {}, 0
        for st in states:
            for j in st["jobs"].values():
                if j["status"] == "working":
                    self.writers[st["repo"]] = self.writers.get(st["repo"], 0) + 1
                if j.get("heavy") and j["status"] in ("gating", "integrating"):
                    self.heavy += 1

    def writer(self, repo: str) -> bool:
        if self.writers.get(repo, 0) >= self.limits["writers_per_repo"] or \
                sum(self.writers.values()) >= self.limits["writers_total"]:
            return False
        self.writers[repo] = self.writers.get(repo, 0) + 1
        return True

    def gate(self, heavy: bool) -> bool:
        if not heavy:
            return True
        if self.heavy >= self.limits["heavy_gates"]:
            return False
        self.heavy += 1
        return True


def schedule(c: Ctx, slots: Slots) -> None:
    st = c.st
    for job, j in st["jobs"].items():
        s = j["status"]
        try:
            if s == "pending":
                deps = [st["jobs"][d]["status"] for d in c.mjobs[job].get("after") or []]
                if "blocked" in deps:
                    block(c, job, "a job it depends on is blocked")
                elif all(d == "merged" for d in deps) and slots.writer(st["repo"]):
                    start_worker(c, job)
            elif s == "needs_worker" and slots.writer(st["repo"]):
                start_worker(c, job)
            elif s == "needs_gate" and slots.gate(j["heavy"]):
                start_gate(c, job)
            elif s == "needs_pr":
                ensure_pr(c, job)
            elif s == "needs_review":
                start_review(c, job)
            elif s == "needs_integrate" and slots.gate(j["heavy"]):
                start_integrate(c, job)
            elif s == "needs_merge":
                do_merge(c, job)
            elif s == "needs_judge":
                start_judge(c, job)
        except STEP_ERRORS as e:
            _error(c, job, e)


def step(st: dict, states: list[dict], forge, slots: Slots) -> None:
    if st["status"] in ("done", "blocked"):
        return
    if st["status"] == "waiting" and not try_start(st, states):
        return
    d = paths.run_dir(st["repo"], st["slug"])
    if not plan_ok(st):
        if st["status"] != "held":
            st["status"] = "held"
            run.alert(st, "the plan changed after its approval: nothing new is dispatched. Running "
                          "processes finish; their results wait until the approved plan is back.")
        proc.reap(d)
        return
    if st["status"] == "held":
        st["status"] = "running"
        run.event(st, "the approved plan is back: running again")
    c = Ctx(st, forge)
    consume_receipts(c)
    schedule(c, slots)


def status_line(states: list[dict]) -> str:
    live = [s for s in states if s["status"] not in ("done", "blocked")]
    finished = len(states) - len(live)
    parts = []
    for s in live:
        n, m = len(s["jobs"]), sum(j["status"] == "merged" for j in s["jobs"].values())
        parts.append(f"{s['repo']}/{s['slug']} {s['status']}" + (f" {m}/{n} merged" if n else ""))
    line = " · ".join(parts) or "idle"
    return line + (f" · {finished} finished" if finished else "")


def cycle(forge=None) -> tuple[str, list[str], bool]:
    """One pass. Returns (status line, alerts to show, whether any run state changed)."""
    forge = forge or forge_mod.get()
    states, before = {}, {}
    for a in approvals():
        key = (a["repo"], a["slug"])
        st = run.load(*key)
        before[key] = json.dumps(st, sort_keys=True) if st else ""
        states[key] = st or run.new_state(a)
    slots = Slots(states.values(), paths.load_workspace()["limits"])
    for st in states.values():
        try:
            step(st, list(states.values()), forge, slots)
            st.pop("error", None)
        except Exception as e:  # one broken run must not stop the others
            text = f"{type(e).__name__}: {e}"
            if st.get("error") != text:
                st["error"] = text
                run.alert(st, f"runner error: {text}")
    alerts, changed = [], False
    for key, st in states.items():
        alerts += st["alerts"]
        st["alerts"] = []
        if json.dumps(st, sort_keys=True) != before[key]:
            changed = True
            run.save(st)
    return status_line(list(states.values())), alerts, changed
```

Also create the wrapper the loop starts for integration gates (Task 13 adds the others the same way) — `bin/ark-integrate`:

```bash
#!/usr/bin/env bash
# ark-integrate <repo>/<slug> <job> <head-sha> — the integration gate for one job head (§5).
set -euo pipefail
CODE="$(cd "$(dirname "$(realpath "$0")")/.." && pwd)"
export PYTHONPATH="$CODE/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "$CODE/.venv/bin/python" -m ark.integrate "$@"
```

- [ ] **Step 5: Run to verify it passes**

Run: `chmod +x tests/fake_claude.py bin/ark-integrate && .venv/bin/python -m pytest tests/test_loop.py`
Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/loop.py tests/fake_claude.py tests/test_loop.py bin/ark-integrate
git -C ~/.claude/arkimedes commit -q -m "feat(ark): ark-loop state machine: runs, workers, gates, reviews, integration, merges"
```

---

### Task 12: Closing a run — decisions file and final PR

**Files:**
- Modify: `lib/ark/loop.py`, `tests/test_loop.py`

When every job is `merged` or `blocked` and nothing runs, the run closes (§9): one sonnet tick writes the decisions file from the reports and events (the runner writes a plain fallback if the tick fails), the runner commits it to `docs/decisions/<slug>.md` on `ark/<slug>`, opens the final PR to the default branch when anything merged, and the run ends `done` (all merged) or `blocked`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_loop.py`:

```python
def run_finished():
    st = state()
    return bool(st) and st["status"] in ("done", "blocked")


def test_run_closes_with_decisions_and_final_pr(feature):
    repo, _ = feature()
    drive(run_finished)
    st = state()
    assert st["status"] == "done"
    gitops.fetch(repo)
    text = gitops.git(repo, "show", f"origin/ark/{SLUG}:docs/decisions/{SLUG}.md")
    assert text.startswith("# Decisions")
    final = forge.LocalForge().list(repo)[-1]
    assert (final["base"], final["head"], final["title"]) == ("master", f"ark/{SLUG}", "feat(notes): edit and delete notes")
    assert final["body"].startswith(f"**Decisions:** `docs/decisions/{SLUG}.md`")
    assert st["final_pr"]["url"] == final["url"]
    assert "Status: done" in paths.run_file("demo", SLUG).read_text()


def test_blocked_run_writes_the_fallback_when_the_tick_fails(feature, monkeypatch):
    monkeypatch.setenv("FAKE_OUTSIDE", "shell")
    repo, _ = feature()
    monkeypatch.setattr(loop.brief, "decisions_prompt", lambda st, reports: "say nothing useful")
    drive(run_finished)
    st = state()
    assert st["status"] == "blocked" and st["final_pr"] is None             # nothing merged: no final PR
    gitops.fetch(repo)
    text = gitops.git(repo, "show", f"origin/ark/{SLUG}:docs/decisions/{SLUG}.md")
    assert "written by the runner" in text and "outside its area" in text
```

(The fake's tick answers a judge line to any prompt that is not a decisions prompt, so the patched prompt gives an unusable decisions answer.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_loop.py -k "closes or fallback"`
Expected: FAIL — `AssertionError: not reached` (runs never leave `running`).

- [ ] **Step 3: Implement** — in `lib/ark/loop.py`, add after `start_judge`:

```python
# ---- closing ----------------------------------------------------------------------------------

def maybe_close(c: Ctx) -> None:
    st = c.st
    if any(j["status"] not in run.TERMINAL for j in st["jobs"].values()) or proc.active(c.dir):
        return
    if st["closing"] is None:
        reports = {jid: c.read_report(jid) or "" for jid in st["jobs"]}
        cmd = claude.agent_cmd("tick", claude.TICK_MODEL["decisions"], brief.decisions_prompt(st, reports),
                               session=str(uuid.uuid4()), usd=1)
        proc.start(c.dir, "_decisions.tick", cmd, cwd=ticks_dir(), env=claude.runner_env("tick"), minutes=15)
        st["closing"] = "decisions"
        run.event(st, "every job is merged or blocked: writing the decisions file")
    elif st["closing"] == "publish":
        publish(c)


def on_decisions(c: Ctx, r: dict) -> None:
    text = (r["result"] or "").strip() if r["exit"] == 0 and not r["reason"] else ""
    if not text.startswith("# Decisions"):
        reports = {jid: c.read_report(jid) or "" for jid in c.st["jobs"]}
        text = brief.fallback_decisions(c.st, reports)
        run.event(c.st, "the decisions tick gave no file: the runner wrote the fallback")
    (c.dir / "decisions.md").write_text(text.rstrip() + "\n", encoding="utf-8")
    c.st["closing"] = "publish"


def publish(c: Ctx) -> None:
    """Commit docs/decisions/<slug>.md on ark/<slug>, open the final PR, finish. Retried each cycle."""
    st = c.st
    try:
        if not st.get("decisions_sha"):
            st["decisions_sha"] = gitops.commit_file(
                c.repo, c.base, f"docs/decisions/{st['slug']}.md", (c.dir / "decisions.md").read_text(encoding="utf-8"),
                f"docs: decisions for {st['slug']}", paths.worktrees_dir() / st["repo"] / st["slug"] / "_decisions")
            run.event(st, f"decisions file committed on ark/{st['slug']}")
        merged = any(j["status"] == "merged" for j in st["jobs"].values())
        if merged and not st["final_pr"]:
            st["final_pr"] = c.forge.create(c.repo, base=st["default_branch"], head=c.base,
                                            title=c.plan.manifest["final_pr_title"], body=brief.final_pr_body(st))
    except STEP_ERRORS as e:
        text = f"{type(e).__name__}: {e}"
        if st.get("error") != text:
            st["error"] = text
            run.event(st, f"closing: {text} (retrying)")
        return
    all_merged = all(j["status"] == "merged" for j in st["jobs"].values())
    st.update(status="done" if all_merged else "blocked", closing="done")
    pr = f"final PR {st['final_pr']['url']}" if st["final_pr"] else "no job merged, no final PR"
    run.alert(st, f"run {st['status']}: {pr}")
```

In `consume_receipts` nothing changes (`_decisions` is already routed to `on_decisions`). In `step`, add as its last line, after `schedule(c, slots)`:

```python
    maybe_close(c)
```

In `cycle`, the `st.pop("error", None)` after a clean `step` would also wipe a `publish` retry note; change that line to:

```python
            if st.get("closing") != "publish":
                st.pop("error", None)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_loop.py`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/loop.py tests/test_loop.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): close a run: decisions file on ark/<slug> and the final PR"
```

---

### Task 13: `ark-loop` main, wrappers and pointers

**Files:**
- Modify: `lib/ark/loop.py`, `tests/test_bin.py`, `~/.claude/agents/arkimedes.md`
- Create: `bin/ark-loop`, `bin/ark-merge`, `bin/ark-dispatch`, `~/.local/bin/ark-loop` (symlink), `~/.claude/projects/-Users-marcozanger-Documents-workspace-humanharness/memory/push-permission.md`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_bin.py`:

```python
import fcntl


def test_ark_loop_once_idle(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                       env=env_for(ark_home, ARK_FORGE="local"))
    assert r.returncode == 0, r.stderr
    assert "idle" in r.stdout


def test_ark_loop_refuses_an_api_key(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                       env=env_for(ark_home, ANTHROPIC_API_KEY="sk-x"))
    assert r.returncode == 2 and "ANTHROPIC_API_KEY" in r.stderr


def test_ark_loop_runs_once_at_a_time(ark_home):
    (ark_home / "runs").mkdir()
    with open(ark_home / "runs" / ".loop.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = subprocess.run([str(CODE / "bin/ark-loop"), "--once"], capture_output=True, text=True,
                           env=env_for(ark_home))
    assert r.returncode == 1 and "ya está corriendo" in r.stderr


def test_ark_dispatch_only_stops(ark_home):
    r = subprocess.run([str(CODE / "bin/ark-dispatch"), "run", "x"], capture_output=True, text=True,
                       env=env_for(ark_home))
    assert r.returncode == 2 and "uso" in r.stderr
    r = subprocess.run([str(CODE / "bin/ark-dispatch"), "stop", "demo/x", "shell"], capture_output=True,
                       text=True, env=env_for(ark_home))
    assert r.returncode == 1 and "nada corriendo" in r.stdout
```

Append to `tests/test_loop.py`:

```python
def test_idle_cycle_and_drafts(runner, make_feature):
    runner()
    make_feature(plan_text(), create_repo=False)          # a draft: never picked up
    line, alerts, changed = loop.cycle()
    assert line == "idle" and alerts == [] and changed is False
    assert not paths.run_dir("demo", SLUG).exists()


def test_signature_changes_with_an_approval(runner, make_feature):
    runner()
    p = make_feature(plan_text(), create_repo=False)
    before = loop.signature()
    approve(p)
    assert loop.signature() != before
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bin.py tests/test_loop.py -k "loop or dispatch or idle or signature"`
Expected: FAIL — `bin/ark-loop` does not exist, and `AttributeError: module 'ark.loop' has no attribute 'signature'`.

- [ ] **Step 3: Implement** — append to `lib/ark/loop.py` (add `fcntl`, `hashlib`, `os`, `sys`, `time` and `from datetime import datetime` to the imports):

```python
# ---- main ---------------------------------------------------------------------------------------

def signature() -> str:
    """Cheap fingerprint of what can wake the loop: approvals, plan files, receipts, processes past
    their deadline or dead without a receipt. A change runs a cycle before the next full tick."""
    h = hashlib.sha256()
    for f in sorted(paths.approvals_dir().glob("**/*.json")):
        h.update(f"{f}:{f.stat().st_mtime_ns}\n".encode())
    for a in approvals():
        p = Path(a["plan"])
        h.update(f"{p}:{p.stat().st_mtime_ns if p.exists() else 0}\n".encode())
    for f in sorted(paths.runs_dir().glob("**/*.receipt.json")):
        h.update(f"{f}\n".encode())
    for d in sorted({f.parent for f in paths.runs_dir().glob("**/*.proc")}):
        for n in proc.overdue(d):
            h.update(f"{d}/{n}:overdue\n".encode())
        for n in proc.active(d):
            info = proc._read(d / f"{n}.proc")
            if info and not (d / f"{n}.receipt.json").exists() and not proc._alive(d, n, info["pid"]):
                h.update(f"{d}/{n}:dead\n".encode())
    return h.hexdigest()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("ark-loop no corre con ANTHROPIC_API_KEY: los workers facturarían por token en vez de usar "
              "tu suscripción. Hacé `unset ANTHROPIC_API_KEY` y volvé a correrlo.", file=sys.stderr)
        return 2
    if os.environ.get("ARK_ROLE"):
        print("ark-loop no corre dentro de un proceso del runner.", file=sys.stderr)
        return 2
    paths.runs_dir().mkdir(parents=True, exist_ok=True)
    lock = open(paths.runs_dir() / ".loop.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("ark-loop ya está corriendo.", file=sys.stderr)
        return 1
    once = "--once" in argv
    ws = paths.load_workspace()
    last_line, last_sig, last_full = None, None, 0.0
    while True:
        sig = signature()
        if once or sig != last_sig or time.time() - last_full >= ws["tick_minutes"] * 60:
            for _ in range(5):                       # follow-up passes while things move
                line, alerts, changed = cycle()
                for a in alerts:
                    print(f"⚠ {a}", flush=True)
                if not changed:
                    break
            last_full, last_sig = time.time(), signature()
            if line != last_line:
                print(f"{datetime.now():%H:%M} {line}", flush=True)
                last_line = line
        if once:
            return 0
        time.sleep(ws["poll_seconds"])


if __name__ == "__main__":
    sys.exit(main())
```

`bin/ark-loop`:

```bash
#!/usr/bin/env bash
# ark-loop — the always-on runner. Leave it running in a terminal; Ctrl-C stops it (running
# workers keep going and are picked up when it starts again). --once: one cycle, then exit.
set -euo pipefail
CODE="$(cd "$(dirname "$(realpath "$0")")/.." && pwd)"
export PYTHONPATH="$CODE/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "$CODE/.venv/bin/python" -m ark.loop "$@"
```

`bin/ark-merge`:

```bash
#!/usr/bin/env bash
# ark-merge <repo>/<slug> <job> — merge one job PR into ark/<slug> if every §7 check holds.
set -euo pipefail
CODE="$(cd "$(dirname "$(realpath "$0")")/.." && pwd)"
export PYTHONPATH="$CODE/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "$CODE/.venv/bin/python" -m ark.merge "$@"
```

`bin/ark-dispatch`:

```bash
#!/usr/bin/env bash
# ark-dispatch stop <repo>/<slug> <job> — stop a job's running process; the job ends blocked.
set -euo pipefail
CODE="$(cd "$(dirname "$(realpath "$0")")/.." && pwd)"
if [ "${1:-}" != stop ]; then
  echo "uso: ark-dispatch stop <repo>/<slug> <job>" >&2
  exit 2
fi
export PYTHONPATH="$CODE/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "$CODE/.venv/bin/python" -m ark.proc "$@"
```

```bash
chmod +x ~/.claude/arkimedes/bin/ark-loop ~/.claude/arkimedes/bin/ark-merge ~/.claude/arkimedes/bin/ark-dispatch
ln -sf ~/.claude/arkimedes/bin/ark-loop ~/.local/bin/ark-loop
```

Append to `~/.claude/agents/arkimedes.md`:

```markdown
## Always-on mode

Approved desk plans run without a session: `ark-loop` (leave it in a terminal) picks up every
`ark approve`, runs the jobs with headless workers and merges them into `ark/<slug>`. Its state is
in `~/.claude/arkimedes/runs/<repo>/<slug>.md` (`ark status` shows it). `ark-dispatch stop
<repo>/<slug> <job>` stops a job. Design: `~/.claude/arkimedes/specs/2026-11-05-always-on-design.md`.
```

`~/.claude/projects/-Users-marcozanger-Documents-workspace-humanharness/memory/push-permission.md` (and one line for it in that folder's `MEMORY.md`):

```markdown
---
name: push-permission
description: Arkimedes' runner may push and merge only ark/* branches of approved plans
metadata:
  type: project
---

Since 2026-11-05 `ark-loop` (Arkimedes always-on) may push `ark/<slug>` and `ark/<slug>-<job>`
branches and squash-merge job PRs into `ark/<slug>` for plans Marco approved with `ark approve`.
The default branch stays Marco's: the runner only opens the final PR to it.

**Why:** Marco approved the always-on design (specs/2026-11-05-always-on-design.md §7).
**How to apply:** a commit or merge on `ark/*` by the runner is expected, not a rule break.
Pushing to the default branch is still never allowed.
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/loop.py bin/ark-loop bin/ark-merge bin/ark-dispatch tests/test_bin.py tests/test_loop.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): ark-loop main (polling, lock, API-key refusal) and wrappers"
```

(`~/.claude/agents/arkimedes.md` and the memory note are outside this repo; nothing to commit for them.)

---

### Task 14: End-to-end scenarios across runs

**Files:**
- Modify: `tests/test_loop.py`

The §12 runner tests that involve more than one run, or a plan that changes under a run.

- [ ] **Step 1: Write the tests** — append to `tests/test_loop.py`:

```python
def test_writer_limit_holds_across_repos(feature, ark_home, monkeypatch):
    ws = json.loads((ark_home / "workspace.json").read_text())
    ws["limits"] = {"writers_total": 1}
    (ark_home / "workspace.json").write_text(json.dumps(ws))
    monkeypatch.setenv("FAKE_SLEEP", "0.5")
    one = manifest(jobs=[manifest()["jobs"][0]])
    feature(one, repo="demo", tasks=("T1", "T2"))                 # T3 belongs to no job here
    feature(one, repo="other", tasks=("T1", "T2"))
    drive(lambda: jobs_settled("demo") and jobs_settled("other"))
    assert state("demo")["jobs"]["shell"]["status"] == "merged"
    assert state("other")["jobs"]["shell"]["status"] == "merged"
    assert (paths.ark_home().parent / "fake" / "max_active").read_text() == "1"


def test_overlapping_plans_in_one_repo_wait(feature, make_feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "0.3")
    feature()
    p2 = make_feature(plan_text(slug="notes-more"), slug="notes-more", create_repo=False)
    approve(p2, slug="notes-more", at="2026-11-05T11:00:00+00:00")
    loop.cycle()
    assert state()["status"] == "running"
    second = state(slug="notes-more")
    assert second["status"] == "waiting" and "overlaps the running plan notes-edit" in second["reason"]
    drive(run_finished)
    drive(lambda: state(slug="notes-more")["status"] in ("done", "blocked"))
    assert state(slug="notes-more")["status"] == "done"


def test_changed_plan_holds_the_run(feature, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    repo, plan = feature()
    loop.cycle()
    assert state()["jobs"]["shell"]["status"] == "working"
    plan.write_text(plan.read_text() + "\nedited after approval\n")
    line, alerts, _ = loop.cycle()
    assert state()["status"] == "held" and "held" in line
    assert any("changed after its approval" in a for a in alerts)
    assert "Status: held" in paths.run_file("demo", SLUG).read_text()
    time.sleep(1.5)
    loop.cycle()
    st = state()
    assert st["status"] == "held" and st["jobs"]["shell"]["status"] == "working"   # receipt waits
    assert gitops.remote_sha(repo, f"ark/{SLUG}-shell") is None
    d = paths.run_dir("demo", SLUG)
    assert proc.finished(d) == ["shell.worker"]
```

(`paths.ark_home().parent / "fake"` is the `FAKE_STATE` folder the `runner` fixture made: both live in `tmp_path`.)

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest tests/test_loop.py`
Expected: `14 passed`. These test behaviour built in Tasks 11–13; if one fails, the bug is in `loop.py` — fix it there, not in the test.

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, in under 2 minutes.

- [ ] **Step 4: Commit**

```bash
git -C ~/.claude/arkimedes add tests/test_loop.py
git -C ~/.claude/arkimedes commit -q -m "test(ark): runner scenarios across runs: limits, overlap, held plan"
```

---

### Task 15: Manual smoke with Marco on `ark-sandbox`

**Files:** none in this repo except the result note at the end. Run each step with Marco; he confirms the ones marked **(Marco)**.

- [ ] **Step 1: Create the sandbox repo (Marco)**

```bash
cd ~/Documents/workspace && mkdir ark-sandbox && cd ark-sandbox && git init -q -b main
printf '# ark-sandbox\n\nA throwaway repo for Arkimedes runner tests.\n' > README.md
mkdir -p src tests && printf 'def add(a, b):\n    return a + b\n' > src/calc.py
printf 'from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n' > tests/test_calc.py
touch src/__init__.py
git add -A && git commit -q -m "init"
gh repo create ark-sandbox --private --source . --push
git remote set-head origin --auto
```

Expected: `gh repo view ark-sandbox` shows a private repo; `ark status` lists nothing for it yet.

- [ ] **Step 2: Plan two jobs through the desk (Marco)**

In a `planner` session: a feature `ark-sandbox/calc-ops` with `spec: none`, two jobs:
`sub` (T0, area `src/sub.py`, `tests/test_sub.py`: add `sub(a, b)` with a test) and `mul` (T1, area `src/mul.py`, `tests/test_mul.py`, `after: [sub]`). Gates `python3 -m pytest -q tests`, `integration_gate: python3 -m pytest -q`. `ark validate ark-sandbox/calc-ops` → `OK`. Marco runs `ark approve ark-sandbox/calc-ops`.

- [ ] **Step 3: Run the loop (Marco, in a separate terminal)**

```bash
unset ANTHROPIC_API_KEY; ark-loop
```

Expected within 15 s: `ark-sandbox/calc-ops running 0/2 merged`. Watch `~/.claude/arkimedes/runs/ark-sandbox/calc-ops.md` for the events.

- [ ] **Step 4: Check the run**

Wait for `⚠ ark-sandbox/calc-ops: run done: final PR https://github.com/...`. Then check:

```bash
gh pr list -R "$(gh repo view ark-sandbox --json nameWithOwner -q .nameWithOwner)" --state all
git -C ~/Documents/workspace/ark-sandbox fetch -q && git -C ~/Documents/workspace/ark-sandbox show origin/ark/calc-ops:docs/decisions/calc-ops.md
git -C ~/Documents/workspace/ark-sandbox log --oneline origin/main -1
```

Expected: two job PRs merged into `ark/calc-ops` with the manifest titles, one open final PR to `main` whose body starts with the decisions path, the decisions file in the §9 format, `main` unchanged.

- [ ] **Step 5: Check the guards against a live worker**

Look at the worker logs in `runs/ark-sandbox/calc-ops/done/*.worker-*.log`:
- no tool call wrote outside the job's area (any attempt shows a `permissionDecision: deny` from area-guard);
- no `git push` / `gh pr` ran from a worker;
- the coach hooks from `~/.claude/settings.json` (`coach/session-start.sh`, `coach/prompt.sh`) ran in workers: if their output shows up in the worker transcript, note it — P3 decides whether `worker-settings.json` should turn them off for `ARK_ROLE`;
- `--permission-mode auto` let the worker run tests and commit without prompts.

Then start a second, trivial plan, and while its worker runs: `ark-dispatch stop ark-sandbox/<slug> <job>` → `detenido: <job>.worker`, and the next cycle shows the job `blocked` with `stopped by Marco`.

- [ ] **Step 6: Record the result**

Append a `## P2 smoke results (YYYY-MM-DD)` section to this plan with each step's outcome and anything that surprised, then:

```bash
git -C ~/.claude/arkimedes add specs/2026-11-05-p2-runner-plan.md
git -C ~/.claude/arkimedes commit -q -m "docs(ark): P2 smoke results"
```

Clean up when Marco says so: `gh repo delete ark-sandbox` is his call, not the session's.

---

## Self-review against the spec

| Spec | Where |
|---|---|
| §4 ark-loop: picks up approvals, cheap wake-up, full tick every 15 min | Task 11 (`cycle`, `approvals`), Task 13 (`signature`, polling `main`) |
| §4 tick kinds | Narrowed by Marco's "scripts first": `judge` (Task 11) and `decisions` (Task 12); intervene ticks are P3 |
| §4 runs survive a loop restart | Task 6 (detached processes + receipts), Task 11 (state in `state.json`) |
| §5 run start: validate, `ark/<slug>` from the default branch, area overlap with other runs | Task 11 `try_start` |
| §5 one job = one worktree, one branch, one PR, one squash-merge | Tasks 4, 5, 11 |
| §5 `after` order, blocked deps block | Task 11 `schedule` |
| §5 job gate, then reviewer (T1; T2 for T2 jobs), FIX → worker | Task 11 `on_gate`, `start_review`, `on_review` |
| §5 integration gate before every merge; red → worker once, then blocked | Task 10 `integrate`, Task 11 `on_integrate` |
| §5 tier escalation T0 → T1 → T2 | Task 11 `allowed_actions`, `on_judge` |
| §5 limits: writers per repo / total, heavy gates | Task 1 (defaults), Task 11 `Slots`, Task 14 test |
| §6 area-guard, read-guard, merge-guard, done-gate (3 blocks) | Task 9 |
| §6 over-time limits per tier, died detection | Task 6 `reap`, Task 8 `TIER_MINUTES` |
| §6 `ark-dispatch stop` | Task 6 `main`, Task 13 wrapper |
| §6 no API key | Task 8 `runner_env`, Task 13 `main` |
| §7 merge checks, in code, only path to `ark/<slug>` | Task 10 `check` / `merge`; Task 4 `_ark_only`; Task 9 merge-guard |
| §9 decisions file on `ark/<slug>`, final PR names it | Task 7 prompts and fallback, Task 12 |
| §10 mechanics are scripts; ticks see only their decision; workers get only their job | Tasks 7, 11, 12; Task 9 `session-start.sh` early exit |
| §12 runner tests | Tasks 6–14 (fake claude, local forge) |
| Plan changed after approval | Task 11 `step` → `held`; re-approval is P3 |

## Left for P3

- Stalled and over-token detection; intervene ticks; `steer`.
- `ark ps`, `ark logs`, `ark attach`.
- `questions.md` and `ark answer` (a BLOCKED worker's question reaches Marco as the judge's block note, in P2).
- Phone notifications (P2 prints alerts in the `ark-loop` terminal).
- Spending caps per day / per run beyond `--max-budget-usd` per process.
- Re-approval of a changed plan while its run is held.
- `steps.jsonl` and `history/` (insights, lessons, repo analysis).
- Whether runner processes should skip Marco's user hooks (see the Task 15 smoke note).
