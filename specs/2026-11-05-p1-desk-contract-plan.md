# Arkimedes always-on — P1: desk and plan contract — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Marco can write specs and plans per repo in `~/.claude/arkimedes/desk/`, check them against the plan contract with `ark validate`, and close them with `ark approve` (which he always confirms), while no runner process can approve or edit a plan.

**Architecture:** `~/.claude/arkimedes` becomes a git repo with a private GitHub remote. Deterministic logic is a small Python package (`lib/ark/`) run from a local `.venv` (PyYAML + pytest); `bin/ark` is a thin bash wrapper that also keeps the old `ark` alias behaviour (interactive Arkimedes). Hooks: `desk-start.sh` (overview at session start in the folder) and `desk-guard.py` (user-level: approvals only through `ark approve`, desk read-only for runner processes). The `writing-plans` skill learns to write desk plans.

**Tech Stack:** Python 3.14 (Homebrew) + PyYAML + pytest in `~/.claude/arkimedes/.venv`; bash + jq for wrappers and hooks; git + gh.

**Spec:** `~/.claude/arkimedes/specs/2026-11-05-always-on-design.md` — this plan covers §2, §2a, §2b, the `desk/`/`approvals/` part of §7, and the matching §12 tests.

**Roadmap (one plan each, stop between plans):**
- **P1 — Desk and plan contract (this plan).**
- P2 — Runner: `workspace.json` limits, `ark-loop` (lock, pre-check, fswatch, tick classification), `ark-dispatch start/stop`, worker settings with `area-guard`, `read-guard`, `done-gate`, `merge-guard`, `ark-merge`, `ark-integrate`, tick-mode session start, final PR, decisions file, `ark-dev`/`ark-reviewer`, push-permission memory note.
- P3 — Control and questions: anomalies, intervene ticks, `steer`, `ark ps/logs/stop/attach`, `questions.md` + `ark answer`, phone notifications, spending caps, re-approval question on a stale sha.
- P4 — Report and history: `steps.jsonl`, `ark-report`, `ark-stats`, `insights.md`, `lessons.md`, Artifact publish.
- P5 — Squad and onboarding: `squad.yaml`, `ark add`, onboard tick, squad PR, tracker guards, squad defaults for `gate`/`setup`/`integration_gate`.

**Decisions taken while planning (Marco, 2026-11-05):** 5 plans as above; Python + venv; `git init` + private GitHub remote.

**Open this plan's executing session in `~/.claude/arkimedes`** (`cd ~/.claude/arkimedes && claude`). The global `require-worktree.sh` hook judges mutating Bash commands by the session's cwd; under `~/.claude/` they are allowed, from a repo's main checkout they are denied.

---

## File structure

| Path (under `~/.claude/arkimedes/`) | Responsibility |
|---|---|
| `.gitignore`, `requirements.txt`, `pytest.ini` | Repo and test setup |
| `lib/ark/__init__.py` | Package marker |
| `lib/ark/paths.py` | `ARK_HOME`, desk/approvals/runs paths, `workspace.json`, repo name → folder |
| `lib/ark/plan.py` | Parse a plan: frontmatter, `##` sections, `### T<n>` tasks, yaml Run manifest; `set_status`; `sha256_file` |
| `lib/ark/globs.py` | Conservative overlap test between area globs |
| `lib/ark/validate.py` | The plan contract, checks 1–6 (spec §2b) |
| `lib/ark/desk.py` | List feature folders with spec / plan / approval / run state |
| `lib/ark/cli.py` | `status`, `plans`, `validate`, `approve` |
| `bin/ark`, `bin/ark-validate` | Wrappers; `ark` with no args = interactive Arkimedes, `ark plan` = desk session |
| `hooks/desk-start.sh` | SessionStart in the folder: inject `ark status` |
| `hooks/desk-guard.py` | User-level PreToolUse: protect `approvals/` always, `desk/` from runner processes |
| `.claude/settings.json` | Project settings of the folder: registers `desk-start.sh` |
| `CLAUDE.md`, `templates/spec.md`, `templates/plan.md` | Desk mode instructions and formats |
| `workspace.json` | Workspace roots (limits are added in P2/P3) |
| `tests/helpers.py`, `tests/conftest.py`, `tests/test_*.py` | pytest suite |

Outside the folder: `~/.claude/settings.json` (ask rule + desk-guard hook), `~/.zshrc` (drop the `ark` alias), `~/.local/bin/ark` (symlink), `~/.claude/agents/arkimedes.md` (desk mode section), `~/.claude/skills/writing-plans/SKILL.md` (desk plans).

Conventions: code, comments, validator messages and files in English; CLI text meant for Marco in Spanish. Tests run with `.venv/bin/python -m pytest -q` from `~/.claude/arkimedes`. Every commit message ends with the attribution lines from your context.

---

### Task 1: Repo bootstrap (git, venv, private remote)

**Files:**
- Create: `.gitignore`, `requirements.txt`, `pytest.ini`, `lib/ark/__init__.py`, `tests/test_smoke.py`

- [ ] **Step 1: Write the setup files**

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
*.tmp
runs/
approvals/
history/
repos/
questions.md
log.jsonl
```
(`desk/` and `specs/` are versioned: plans get a backup on the private remote. Runtime state is not.)

`requirements.txt`:
```
PyYAML==6.0.2
pytest==8.3.4
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
pythonpath = lib tests
addopts = -q
```

`lib/ark/__init__.py`:
```python
"""Arkimedes: deterministic pieces (no model) of the desk and the runner."""
```

`tests/test_smoke.py`:
```python
import yaml

import ark


def test_package_and_yaml_import():
    assert ark.__doc__
    assert yaml.safe_load("a: 1") == {"a": 1}
```

- [ ] **Step 2: Create the venv and run the smoke test**

Run:
```bash
cd ~/.claude/arkimedes
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m pytest
```
Expected: `1 passed`.

- [ ] **Step 3: git init, first commit, private remote**

Before committing, check no secrets are tracked: `grep -rniE 'token|secret|api[_-]?key|password' --include='*.json' --include='*.sh' --include='*.md' . | grep -v '.venv'` must show nothing sensitive (mentions in specs are fine).

```bash
cd ~/.claude/arkimedes
git init -q -b main
git add .gitignore requirements.txt pytest.ini lib tests hooks settings.json specs
git commit -q -m "chore: put Arkimedes under git with a Python venv and pytest"
gh repo create marcozanger/arkimedes --private --source . --remote origin --push
git -C ~/.claude/arkimedes log --oneline -1 && git -C ~/.claude/arkimedes remote -v
```
Expected: one commit; `origin` points at `github.com/marcozanger/arkimedes` (private). Work on `main` directly in this repo: it is Arkimedes' own config, not a product repo, and has no worktrees.

---

### Task 2: Plan parser

**Files:**
- Create: `lib/ark/plan.py`, `tests/helpers.py`, `tests/test_plan.py`

- [ ] **Step 1: Write the test helpers**

`tests/helpers.py`:
```python
"""Builders for plan files used across the tests."""
import yaml


def job(id, tasks, area, after=None, **over):
    j = {
        "id": id, "pr_title": f"feat: {id}", "includes": f"{id} work", "excludes": "anything else",
        "tasks": list(tasks), "area": list(area), "after": list(after or []), "tier": "T1",
        "gate": "true", "heavy": False, "done": [f"{id} done"], "budget": {"minutes": 30, "usd": 2},
    }
    j.update(over)
    return j


def manifest(**over):
    m = {
        "setup": [],
        "integration_gate": "true",
        "final_pr_title": "feat(notes): edit and delete notes",
        "jobs": [
            job("shell", ["T1", "T2"], ["src/shell/**"]),
            job("ui", ["T3"], ["src/ui/**"], after=["shell"]),
        ],
    }
    m.update(over)
    return m


def plan_text(m=None, *, tasks=("T1", "T2", "T3"), drop=(), **fm):
    """A complete desk plan. Frontmatter keys passed as None are left out."""
    front = {"status": "draft", "repo": "demo", "slug": "notes-edit", "created": "2026-11-05"}
    front.update(fm)
    front = {k: v for k, v in front.items() if v is not None}
    sections = {
        "Goal": "Edit and delete notes.",
        "Context": "Spec next to this plan.",
        "Tasks": "\n\n".join(f"### {t} — step\n\nDo it, test first." for t in tasks),
        "PR plan": "Shell first, then the UI.",
        "Run manifest": "```yaml\n" + yaml.safe_dump(m if m is not None else manifest(), sort_keys=False) + "```",
    }
    body = "\n\n".join(f"## {k}\n\n{v}" for k, v in sections.items() if k not in drop)
    return "---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n# Notes edit\n\n" + body + "\n"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_plan.py`:
```python
import pytest

from ark.plan import PlanError, load_plan, set_status, sha256_file, split_frontmatter, split_sections
from helpers import plan_text


def test_frontmatter_and_body():
    fm, body = split_frontmatter("---\nstatus: draft\nrepo: demo\n---\n# T\n")
    assert fm == {"status": "draft", "repo": "demo"}
    assert body == "# T\n"


def test_no_frontmatter_is_empty():
    assert split_frontmatter("# T\n") == ({}, "# T\n")


def test_frontmatter_must_be_a_mapping():
    with pytest.raises(PlanError):
        split_frontmatter("---\n- a\n---\n")


def test_sections_ignore_headings_inside_fences():
    secs = split_sections("## A\none\n```\n## not a section\n```\n## B\ntwo\n")
    assert list(secs) == ["A", "B"]
    assert "## not a section" in secs["A"]


def test_load_plan_reads_tasks_and_manifest(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text())
    plan = load_plan(p)
    assert plan.tasks == ["T1", "T2", "T3"]
    assert [j["id"] for j in plan.manifest["jobs"]] == ["shell", "ui"]
    assert plan.manifest_error is None


def test_manifest_missing_block(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text().replace("```yaml", "```text"))
    assert "no ```yaml block" in load_plan(p).manifest_error


def test_set_status_only_touches_frontmatter(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(plan_text() + "\nstatus: draft stays here\n")
    set_status(p, "closed")
    assert load_plan(p).frontmatter["status"] == "closed"
    assert "status: draft stays here" in p.read_text()


def test_sha256_changes_with_content(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("a")
    first = sha256_file(p)
    p.write_text("b")
    assert first != sha256_file(p)
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_plan.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'ark.plan'`.

- [ ] **Step 4: Implement `lib/ark/plan.py`**

```python
"""Read a desk plan: frontmatter, level-2 sections, task ids and the yaml Run manifest."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
TASK = re.compile(r"^### (T\d+)\b", re.M)
YAML_BLOCK = re.compile(r"^```ya?ml[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
HEADING = re.compile(r"^## (.+?)\s*$")


class PlanError(Exception):
    """The file cannot be read as a plan at all."""


@dataclass
class Plan:
    path: Path
    frontmatter: dict
    sections: dict[str, str]
    tasks: list[str]
    manifest: dict | None
    manifest_error: str | None


def split_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise PlanError(f"frontmatter is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PlanError("frontmatter is not a mapping")
    return data, text[m.end():]


def split_sections(body: str) -> dict[str, str]:
    """`## Heading` → its text, ignoring headings inside fenced code."""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        m = None if fenced else HEADING.match(line)
        if m:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


def parse_manifest(section: str) -> tuple[dict | None, str | None]:
    m = YAML_BLOCK.search(section)
    if not m:
        return None, "no ```yaml block in `## Run manifest`"
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        return None, f"Run manifest is not valid YAML: {e}"
    if not isinstance(data, dict):
        return None, "Run manifest must be a YAML mapping"
    return data, None


def load_plan(path) -> Plan:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PlanError(f"cannot read {path}: {e}") from e
    fm, body = split_frontmatter(text)
    secs = split_sections(body)
    manifest, err = parse_manifest(secs.get("Run manifest", ""))
    return Plan(path, fm, secs, TASK.findall(secs.get("Tasks", "")), manifest, err)


def set_status(path, status: str) -> None:
    """Rewrite `status:` in the frontmatter only, atomically."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER.match(text)
    if not m:
        raise PlanError(f"{path} has no frontmatter")
    block = m.group(1)
    if re.search(r"^status:", block, re.M):
        block = re.sub(r"^status:.*$", f"status: {status}", block, count=1, flags=re.M)
    else:
        block = f"status: {status}\n{block}"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text[: m.start(1)] + block + text[m.end(1):], encoding="utf-8")
    tmp.replace(path)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_plan.py`
Expected: `8 passed`.

- [ ] **Step 6: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/plan.py tests/helpers.py tests/test_plan.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): parse desk plans (frontmatter, sections, tasks, run manifest)"
```

---

### Task 3: Area overlap

**Files:**
- Create: `lib/ark/globs.py`, `tests/test_globs.py`

- [ ] **Step 1: Write the failing test**

`tests/test_globs.py`:
```python
import pytest

from ark.globs import areas_overlap, overlap


@pytest.mark.parametrize("a,b,expected", [
    ("src/shell/**", "src/ui/**", False),
    ("src/**", "src/ui/x.ts", True),
    ("src/a.ts", "src/b.ts", False),
    ("src/a.ts", "src/a.ts", True),
    ("**", "anything/at/all", True),
    ("src/*.ts", "src/*.css", True),  # conservative: same folder, wildcard names
    ("./src/ui/", "src/ui/Button.tsx", True),
])
def test_overlap(a, b, expected):
    assert overlap(a, b) is expected
    assert overlap(b, a) is expected


def test_areas_overlap_any_pair():
    assert areas_overlap(["a/**", "b/x"], ["c/**", "b/x"])
    assert not areas_overlap(["a/**"], ["b/**"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_globs.py`
Expected: FAIL — `No module named 'ark.globs'`.

- [ ] **Step 3: Implement `lib/ark/globs.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_globs.py`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/globs.py tests/test_globs.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): conservative overlap test for job areas"
```

---

### Task 4: Paths, workspace and repo lookup

**Files:**
- Create: `lib/ark/paths.py`, `workspace.json`, `tests/conftest.py`, `tests/test_paths.py`

- [ ] **Step 1: Write the fixtures**

`tests/conftest.py`:
```python
import json
import subprocess

import pytest

from helpers import plan_text


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def ark_home(tmp_path, monkeypatch):
    """An empty Arkimedes home whose only workspace root is tmp_path/ws."""
    home = tmp_path / "ark"
    home.mkdir()
    (tmp_path / "ws").mkdir()
    (home / "workspace.json").write_text(json.dumps({"roots": [str(tmp_path / "ws")], "exclude": []}))
    monkeypatch.setenv("ARK_HOME", str(home))
    monkeypatch.delenv("ARK_ROLE", raising=False)
    return home


@pytest.fixture
def make_repo(ark_home, tmp_path):
    def make(name="demo", origin=True):
        repo = tmp_path / "ws" / name
        repo.mkdir(parents=True)
        git("init", "-q", "-b", "master", cwd=repo)
        if origin:
            bare = tmp_path / "remotes" / (name.replace("/", "-") + ".git")
            bare.mkdir(parents=True)
            git("init", "-q", "--bare", cwd=bare)
            git("remote", "add", "origin", str(bare), cwd=repo)
        return repo
    return make


@pytest.fixture
def make_feature(ark_home, make_repo, tmp_path):
    """Write desk/<repo>/<slug>/plan.md (and spec.md unless spec_status is None)."""
    def make(text=None, *, repo="demo", slug="notes-edit", spec_status="approved", create_repo=True):
        if create_repo and not (tmp_path / "ws" / repo).exists():
            make_repo(repo)
        d = ark_home / "desk" / repo / slug
        d.mkdir(parents=True, exist_ok=True)
        if spec_status:
            (d / "spec.md").write_text(
                f"---\nstatus: {spec_status}\nrepo: {repo}\nslug: {slug}\ncreated: 2026-11-05\n---\n# Spec\n")
        p = d / "plan.md"
        p.write_text(text if text is not None else plan_text(repo=repo, slug=slug))
        return p
    return make
```

- [ ] **Step 2: Write the failing test**

`tests/test_paths.py`:
```python
from ark import paths


def test_paths_follow_ark_home(ark_home):
    assert paths.ark_home() == ark_home
    assert paths.approval_file("muscleapp/mobile", "x") == ark_home / "approvals/muscleapp/mobile/x.json"
    assert paths.run_file("demo", "x") == ark_home / "runs/demo/x.md"


def test_find_repo_needs_origin(make_repo):
    with_origin = make_repo("demo")
    make_repo("local-only", origin=False)
    assert paths.find_repo("demo") == with_origin
    assert paths.find_repo("local-only") is None
    assert paths.find_repo("missing") is None


def test_find_repo_one_level_deeper(make_repo):
    nested = make_repo("muscleapp/mobile")
    assert paths.find_repo("muscleapp/mobile") == nested
    assert paths.find_repo("../demo") is None
    assert paths.find_repo("a/b/c") is None


def test_excluded_repo_is_not_found(make_repo, ark_home):
    make_repo("demo")
    (ark_home / "workspace.json").write_text(
        '{"roots": ["%s"], "exclude": ["demo"]}' % (ark_home.parent / "ws"))
    assert paths.find_repo("demo") is None
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_paths.py`
Expected: FAIL — `cannot import name 'paths'`.

- [ ] **Step 4: Implement `lib/ark/paths.py`**

```python
"""Where Arkimedes keeps things, and how a repo name maps to a folder."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def ark_home() -> Path:
    return Path(os.environ.get("ARK_HOME") or Path.home() / ".claude" / "arkimedes").expanduser()


def desk_dir() -> Path:
    return ark_home() / "desk"


def approvals_dir() -> Path:
    return ark_home() / "approvals"


def runs_dir() -> Path:
    return ark_home() / "runs"


def approval_file(repo: str, slug: str) -> Path:
    return approvals_dir() / repo / f"{slug}.json"


def run_file(repo: str, slug: str) -> Path:
    return runs_dir() / repo / f"{slug}.md"


def load_workspace() -> dict:
    f = ark_home() / "workspace.json"
    if not f.exists():
        return {"roots": [], "exclude": []}
    return json.loads(f.read_text(encoding="utf-8"))


def _has_origin(path: Path) -> bool:
    r = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def find_repo(name: str) -> Path | None:
    """`name` is the repo's path under a workspace root: "humanharness" or "muscleapp/mobile".

    It must be a main checkout (`.git` is a folder, so linked worktrees never match) with an
    origin, at most one level deeper than the root, and not excluded.
    """
    parts = Path(name).parts
    if not parts or len(parts) > 2 or ".." in parts or Path(name).is_absolute():
        return None
    ws = load_workspace()
    if name in ws.get("exclude", []):
        return None
    for root in ws.get("roots", []):
        cand = Path(root).expanduser() / name
        if (cand / ".git").is_dir() and _has_origin(cand):
            return cand
    return None
```

`workspace.json` (the real one):
```json
{
  "roots": ["~/Documents/workspace"],
  "exclude": []
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_paths.py`
Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/paths.py workspace.json tests/conftest.py tests/test_paths.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): workspace roots and repo lookup"
```

---

### Task 5: The plan contract (`validate`)

**Files:**
- Create: `lib/ark/validate.py`, `tests/test_validate.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_validate.py`:
```python
from ark.validate import validate
from helpers import job, manifest, plan_text


def checks(path, **kw):
    return sorted({p.check for p in validate(path, **kw)})


def messages(path, **kw):
    return " | ".join(str(p) for p in validate(path, **kw))


def test_valid_plan_has_no_problems(make_feature):
    assert validate(make_feature()) == []


# 1. frontmatter and repo

def test_missing_frontmatter_key(make_feature):
    assert "missing `created`" in messages(make_feature(plan_text(created=None)))


def test_slug_must_match_folder(make_feature):
    assert "slug is 'other'" in messages(make_feature(plan_text(slug="other")))


def test_repo_without_origin(make_feature, make_repo):
    make_repo("local-only", origin=False)
    p = make_feature(plan_text(repo="local-only"), repo="local-only", create_repo=False)
    assert "repo" in checks(p)


def test_unknown_repo(make_feature):
    p = make_feature(plan_text(repo="nope"), repo="nope", create_repo=False)
    assert "repo" in checks(p)


# spec

def test_spec_must_be_approved(make_feature):
    assert "spec" in checks(make_feature(spec_status="draft"))


def test_no_spec_needs_spec_none(make_feature):
    assert "spec" in checks(make_feature(spec_status=None))
    assert validate(make_feature(plan_text(spec="none"), spec_status=None)) == []


def test_spec_path_inside_the_repo(make_feature, make_repo):
    repo = make_repo("demo")
    (repo / "docs").mkdir()
    (repo / "docs/s.md").write_text("x")
    assert validate(make_feature(plan_text(spec="docs/s.md"), spec_status=None, create_repo=False)) == []
    assert "spec" in checks(make_feature(plan_text(spec="docs/missing.md"), spec_status=None, create_repo=False))


# structure

def test_missing_section(make_feature):
    p = make_feature(plan_text(drop=("PR plan",)))
    assert "missing or empty section `## PR plan`" in messages(p)


def test_manifest_needs_integration_gate(make_feature):
    m = manifest()
    del m["integration_gate"]
    assert "missing `integration_gate`" in messages(make_feature(plan_text(m)))


# 2. tasks

def test_task_in_no_job(make_feature):
    assert "T4 is in no job" in messages(make_feature(plan_text(tasks=("T1", "T2", "T3", "T4"))))


def test_task_in_two_jobs(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]),
                       job("ui", ["T2", "T3"], ["src/ui/**"], after=["shell"])])
    assert "T2 is in more than one job" in messages(make_feature(plan_text(m)))


def test_job_names_a_task_that_does_not_exist(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2", "T9"], ["src/shell/**"]),
                       job("ui", ["T3"], ["src/ui/**"], after=["shell"])])
    assert "task T9 is not under" in messages(make_feature(plan_text(m)))


# 3. job fields

def test_job_without_done(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"], done=[]),
                       job("ui", ["T3"], ["src/ui/**"], after=["shell"])])
    assert "shell: missing `done`" in messages(make_feature(plan_text(m)))


def test_bad_tier_and_budget(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2", "T3"], ["src/**"], tier="T5", budget={"minutes": 10})])
    msg = messages(make_feature(plan_text(m)))
    assert "tier must be" in msg
    assert "budget needs" in msg


def test_job_id_must_be_branch_safe(make_feature):
    m = manifest(jobs=[job("Shell IPC", ["T1", "T2", "T3"], ["src/**"])])
    assert "id 'Shell IPC'" in messages(make_feature(plan_text(m)))


# 4. after

def test_after_unknown_job(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/shell/**"]),
                       job("ui", ["T3"], ["src/ui/**"], after=["nope"])])
    assert "unknown job 'nope'" in messages(make_feature(plan_text(m)))


def test_after_cycle(make_feature):
    m = manifest(jobs=[job("a", ["T1"], ["a/**"], after=["c"]),
                       job("b", ["T2"], ["b/**"], after=["a"]),
                       job("c", ["T3"], ["c/**"], after=["b"])])
    assert "cycle:" in messages(make_feature(plan_text(m)))


# 5. areas

def test_parallel_jobs_with_overlapping_areas(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/**"]), job("ui", ["T3"], ["src/ui/**"])])
    assert "shell and ui may run at the same time" in messages(make_feature(plan_text(m)))


def test_overlap_is_fine_when_ordered(make_feature):
    m = manifest(jobs=[job("shell", ["T1", "T2"], ["src/**"]),
                       job("ui", ["T3"], ["src/ui/**"], after=["shell"])])
    assert validate(make_feature(plan_text(m))) == []


def test_overlap_is_fine_through_a_transitive_order(make_feature):
    m = manifest(jobs=[job("a", ["T1"], ["src/**"]),
                       job("b", ["T2"], ["b/**"], after=["a"]),
                       job("c", ["T3"], ["src/c/**"], after=["b"])])
    assert validate(make_feature(plan_text(m))) == []


# 6. approval (the rest is in test_cli.py)

def test_approved_check_needs_a_closed_plan(make_feature):
    assert "plan is not closed" in messages(make_feature(), approved=True)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_validate.py`
Expected: FAIL — `No module named 'ark.validate'`.

- [ ] **Step 3: Implement `lib/ark/validate.py`**

```python
"""The plan contract (spec §2b), checks 1–6. A script, no model.

1 frontmatter + repo · spec · sections · 2 tasks · 3 job fields · 4 after · 5 areas · 6 approval
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .globs import areas_overlap
from .plan import PlanError, load_plan, sha256_file, split_frontmatter

REQUIRED_SECTIONS = ("Goal", "Context", "Tasks", "PR plan", "Run manifest")
JOB_FIELDS = ("pr_title", "includes", "excludes", "area", "gate", "tier", "done", "budget")
TIERS = {"T0", "T1", "T2"}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Problem:
    check: str
    message: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.message}"


class _Cycle(Exception):
    pass


def desk_location(path: Path) -> tuple[str, str] | None:
    """(repo, slug) when the plan sits at desk/<repo>/<slug>/plan.md."""
    try:
        rel = path.resolve().parent.relative_to(paths.desk_dir().resolve())
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    return "/".join(rel.parts[:-1]), rel.parts[-1]


def validate(path, *, approved: bool = False) -> list[Problem]:
    path = Path(path)
    out: list[Problem] = []

    def add(check: str, message: str) -> None:
        out.append(Problem(check, message))

    try:
        plan = load_plan(path)
    except PlanError as e:
        return [Problem("frontmatter", str(e))]
    fm = plan.frontmatter
    repo_path = _check_frontmatter(path, fm, add)
    _check_spec(path, fm, repo_path, add)
    for name in REQUIRED_SECTIONS:
        if not plan.sections.get(name):
            add("sections", f"missing or empty section `## {name}`")
    if plan.manifest_error:
        add("manifest", plan.manifest_error)
    else:
        _check_manifest(plan.manifest, plan.tasks, add)
    if approved:
        _check_approval(path, fm, add)
    return out


def _check_frontmatter(path, fm, add):
    for key in ("status", "repo", "slug", "created"):
        if not fm.get(key):
            add("frontmatter", f"missing `{key}`")
    if fm.get("status") and fm["status"] not in ("draft", "closed"):
        add("frontmatter", f"status must be draft or closed, not {fm['status']!r}")
    if fm.get("slug") and not SAFE_ID.match(str(fm["slug"])):
        add("frontmatter", f"slug {fm['slug']!r} must be lowercase letters, digits and dashes")
    loc = desk_location(path)
    if loc:
        repo, slug = loc
        if fm.get("repo") and str(fm["repo"]) != repo:
            add("frontmatter", f"repo is {fm['repo']!r} but the plan sits under desk/{repo}/")
        if fm.get("slug") and str(fm["slug"]) != slug:
            add("frontmatter", f"slug is {fm['slug']!r} but the folder is {slug!r}")
    if not fm.get("repo"):
        return None
    repo_path = paths.find_repo(str(fm["repo"]))
    if repo_path is None:
        add("repo", f"{fm['repo']!r} is not a git repo with an origin under the workspace roots")
    return repo_path


def _check_spec(path, fm, repo_path, add):
    spec = fm.get("spec")
    if spec == "none":
        return
    if spec is None:
        sibling = path.parent / "spec.md"
        if not sibling.exists():
            add("spec", "no spec.md next to the plan; set `spec: <path>` or `spec: none`")
            return
        try:
            status = split_frontmatter(sibling.read_text(encoding="utf-8"))[0].get("status")
        except PlanError:
            status = None
        if status != "approved":
            add("spec", f"spec.md is {status or 'without status'}, not approved")
        return
    p = Path(str(spec)).expanduser()
    if not p.is_absolute():
        if repo_path is None:
            return  # the repo problem is already reported
        p = repo_path / p
    if not p.exists():
        add("spec", f"spec file not found: {spec}")


def _str_list(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) and x for x in v)


def _positive(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _check_manifest(m, tasks, add):
    for key in ("integration_gate", "final_pr_title"):
        if not m.get(key):
            add("manifest", f"missing `{key}`")
    if "setup" in m and not isinstance(m["setup"], list):
        add("manifest", "`setup` must be a list of commands")
    jobs = m.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        add("manifest", "`jobs` must be a non-empty list")
        return
    byid: dict[str, dict] = {}
    for i, j in enumerate(jobs, 1):
        if not isinstance(j, dict):
            add("jobs", f"job #{i} is not a mapping")
            continue
        jid = str(j.get("id", ""))
        if not SAFE_ID.match(jid):
            add("jobs", f"job #{i}: id {jid!r} must be lowercase letters, digits and dashes")
            continue
        if jid in byid:
            add("jobs", f"duplicate job id {jid!r}")
            continue
        byid[jid] = j
        _check_job(jid, j, add)
    valid = list(byid.values())
    _check_tasks(valid, tasks, add)
    reach = _check_after(valid, add)
    if reach is not None:
        _check_areas(valid, reach, add)


def _check_job(jid, j, add):
    for key in JOB_FIELDS:
        if j.get(key) in (None, "", [], {}):
            add("jobs", f"{jid}: missing `{key}`")
    if j.get("tier") and j["tier"] not in TIERS:
        add("jobs", f"{jid}: tier must be T0, T1 or T2, not {j['tier']!r}")
    for key in ("area", "done"):
        if j.get(key) and not _str_list(j[key]):
            add("jobs", f"{jid}: `{key}` must be a list of non-empty strings")
    b = j.get("budget")
    if b and not (isinstance(b, dict) and _positive(b.get("minutes")) and _positive(b.get("usd"))):
        add("jobs", f"{jid}: budget needs positive numeric `minutes` and `usd`")
    if "heavy" in j and not isinstance(j["heavy"], bool):
        add("jobs", f"{jid}: `heavy` must be true or false")
    if not isinstance(j.get("tasks"), list) or not j["tasks"]:
        add("jobs", f"{jid}: needs at least one task")


def _check_tasks(jobs, tasks, add):
    if not tasks:
        add("tasks", "no `### T<n>` headings under `## Tasks`")
    if len(set(tasks)) != len(tasks):
        add("tasks", "a task id appears twice under `## Tasks`")
    owner: dict[str, list[str]] = {t: [] for t in tasks}
    for j in jobs:
        for t in j.get("tasks") or []:
            t = str(t)
            if t not in owner:
                add("tasks", f"{j['id']}: task {t} is not under `## Tasks`")
                continue
            owner[t].append(j["id"])
    for t, js in owner.items():
        if not js:
            add("tasks", f"{t} is in no job")
        elif len(js) > 1:
            add("tasks", f"{t} is in more than one job: {', '.join(js)}")


def _check_after(jobs, add):
    """Return reach[job] = every job it waits for, directly or not; None on a cycle."""
    ids = {j["id"] for j in jobs}
    deps: dict[str, set[str]] = {}
    for j in jobs:
        after = j.get("after") or []
        if not isinstance(after, list):
            add("after", f"{j['id']}: `after` must be a list")
            after = []
        for a in after:
            if str(a) not in ids:
                add("after", f"{j['id']}: after names unknown job {a!r}")
        deps[j["id"]] = {str(a) for a in after if str(a) in ids}

    reach: dict[str, set[str]] = {}

    def visit(x: str, stack: list[str]) -> set[str]:
        if x in reach:
            return reach[x]
        if x in stack:
            raise _Cycle(stack[stack.index(x):] + [x])
        stack.append(x)
        r: set[str] = set()
        for d in deps[x]:
            r |= {d} | visit(d, stack)
        stack.pop()
        reach[x] = r
        return r

    try:
        for x in deps:
            visit(x, [])
    except _Cycle as c:
        add("after", "cycle: " + " → ".join(c.args[0]))
        return None
    return reach


def _check_areas(jobs, reach, add):
    for i, a in enumerate(jobs):
        for b in jobs[i + 1:]:
            if b["id"] in reach[a["id"]] or a["id"] in reach[b["id"]]:
                continue
            if _str_list(a.get("area")) and _str_list(b.get("area")) and areas_overlap(a["area"], b["area"]):
                add("areas", f"{a['id']} and {b['id']} may run at the same time and their areas overlap; "
                             "split the areas or order them with `after`")


def _check_approval(path, fm, add):
    if fm.get("status") != "closed":
        add("approval", "plan is not closed (run `ark approve`)")
        return
    f = paths.approval_file(str(fm.get("repo")), str(fm.get("slug")))
    if not f.exists():
        add("approval", f"no approval file at {f}")
        return
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        add("approval", f"approval file unreadable: {e}")
        return
    if rec.get("sha256") != sha256_file(path):
        add("approval", "the plan changed after approval; run `ark approve` again")
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_validate.py`
Expected: `22 passed`.

- [ ] **Step 5: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/validate.py tests/test_validate.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): plan contract checks (spec §2b)"
```

---

### Task 6: Desk listing and `ark status` / `plans` / `validate` / `approve`

**Files:**
- Create: `lib/ark/desk.py`, `lib/ark/cli.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:
```python
import json

from ark import cli, paths
from ark.plan import load_plan, sha256_file
from helpers import plan_text


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_approve_closes_the_plan_and_writes_the_approval(make_feature, capsys):
    p = make_feature()
    code, out, _ = run(capsys, "approve", "demo/notes-edit")
    assert code == 0
    assert "Aprobado demo/notes-edit" in out
    assert "shell" in out and "PR final" in out
    assert load_plan(p).frontmatter["status"] == "closed"
    rec = json.loads(paths.approval_file("demo", "notes-edit").read_text())
    assert rec["sha256"] == sha256_file(p)
    assert rec["plan"] == str(p.resolve())
    assert run(capsys, "validate", "demo/notes-edit", "--approved")[0] == 0


def test_approve_refuses_an_incomplete_manifest(make_feature, capsys):
    p = make_feature(plan_text(tasks=("T1", "T2", "T3", "T4")))
    code, _, err = run(capsys, "approve", "demo/notes-edit")
    assert code == 1
    assert "T4 is in no job" in err
    assert load_plan(p).frontmatter["status"] == "draft"
    assert not paths.approval_file("demo", "notes-edit").exists()


def test_approve_refuses_runner_processes(make_feature, capsys, monkeypatch):
    make_feature()
    monkeypatch.setenv("ARK_ROLE", "tick")
    code, _, err = run(capsys, "approve", "demo/notes-edit")
    assert code == 2
    assert "ARK_ROLE" in err
    assert not paths.approval_file("demo", "notes-edit").exists()


def test_an_edit_after_approval_makes_it_stale(make_feature, capsys):
    p = make_feature()
    run(capsys, "approve", "demo/notes-edit")
    p.write_text(p.read_text() + "\nOne more line.\n")
    code, out, _ = run(capsys, "validate", "demo/notes-edit", "--approved")
    assert code == 1
    assert "changed after approval" in out
    assert " stale " in run(capsys, "plans")[1]
    assert run(capsys, "approve", "demo/notes-edit")[0] == 0
    assert " ok " in run(capsys, "plans")[1]


def test_a_draft_plan_is_not_runnable(make_feature, capsys):
    make_feature()
    assert run(capsys, "validate", "demo/notes-edit", "--approved")[0] == 1


def test_status_groups_features_by_repo(make_feature, capsys):
    make_feature()
    make_feature(plan_text(slug="second"), slug="second", spec_status="draft")
    code, out, _ = run(capsys, "status")
    assert code == 0
    assert out.splitlines()[0] == "demo"
    assert "notes-edit" in out and "second" in out and "spec draft" in out


def test_status_on_an_empty_desk(ark_home, capsys):
    assert "Desk vacío" in run(capsys, "status")[1]


def test_validate_accepts_a_path(make_feature, capsys):
    p = make_feature()
    code, out, _ = run(capsys, "validate", str(p))
    assert code == 0 and out.startswith("OK")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py`
Expected: FAIL — `cannot import name 'cli'`.

- [ ] **Step 3: Implement `lib/ark/desk.py`**

```python
"""The desk: one folder per feature at desk/<repo>/<slug>/ with spec.md and/or plan.md."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .plan import PlanError, sha256_file, split_frontmatter


@dataclass
class Feature:
    repo: str
    slug: str
    dir: Path
    spec: str      # spec status, "-" when there is no spec.md, "?" when unreadable
    plan: str      # plan status, same convention
    approval: str  # "-" none · "ok" sha matches · "stale" plan changed after approval
    run: str       # Status: line of runs/<repo>/<slug>.md, "-" when there is no run


def _status(f: Path) -> str:
    if not f.exists():
        return "-"
    try:
        return str(split_frontmatter(f.read_text(encoding="utf-8"))[0].get("status") or "?")
    except (PlanError, OSError):
        return "?"


def _approval(repo: str, slug: str, plan_file: Path) -> str:
    a = paths.approval_file(repo, slug)
    if not a.exists():
        return "-"
    try:
        rec = json.loads(a.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "?"
    if not plan_file.exists():
        return "stale"
    return "ok" if rec.get("sha256") == sha256_file(plan_file) else "stale"


def _run(repo: str, slug: str) -> str:
    f = paths.run_file(repo, slug)
    if not f.exists():
        return "-"
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.startswith("Status:"):
            return line.split(":", 1)[1].strip() or "?"
    return "?"


def features() -> list[Feature]:
    root = paths.desk_dir()
    if not root.is_dir():
        return []
    dirs = sorted({f.parent for name in ("spec.md", "plan.md") for f in root.rglob(name)})
    out: list[Feature] = []
    for d in dirs:
        rel = d.relative_to(root)
        if len(rel.parts) < 2:
            continue
        repo, slug = "/".join(rel.parts[:-1]), rel.parts[-1]
        out.append(Feature(repo, slug, d, _status(d / "spec.md"), _status(d / "plan.md"),
                           _approval(repo, slug, d / "plan.md"), _run(repo, slug)))
    return out
```

- [ ] **Step 4: Implement `lib/ark/cli.py`**

```python
"""`ark` subcommands that need no model: status, plans, validate, approve.

`ark` with no arguments and `ark plan` are handled by bin/ark (they start Claude).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import desk, paths, validate
from .plan import load_plan, set_status, sha256_file


def _resolve(target: str) -> Path:
    p = Path(target).expanduser()
    if p.suffix == ".md":
        return p
    repo, _, slug = target.rstrip("/").rpartition("/")
    if not repo or not slug:
        raise SystemExit(f"ark: «{target}» no es <repo>/<slug> ni la ruta de un plan.md")
    return paths.desk_dir() / repo / slug / "plan.md"


def cmd_status(args) -> int:
    fs = [f for f in desk.features() if not args.repo or f.repo == args.repo]
    if not fs:
        print(f"Sin features para {args.repo}." if args.repo else "Desk vacío: todavía no hay features.")
        return 0
    repo = None
    for f in fs:
        if f.repo != repo:
            repo = f.repo
            print(repo)
        print(f"  {f.slug:<28} spec {f.spec:<9} plan {f.plan:<7} run {f.run}")
    return 0


def cmd_plans(args) -> int:
    fs = [f for f in desk.features() if f.plan != "-"]
    if not fs:
        print("No hay planes en el desk.")
        return 0
    print(f"{'repo/slug':<40} {'plan':<7} {'aprobación':<11} run")
    for f in fs:
        print(f"{f.repo + '/' + f.slug:<40} {f.plan:<7} {f.approval:<11} {f.run}")
    return 0


def cmd_validate(args) -> int:
    path = _resolve(args.target)
    problems = validate.validate(path, approved=args.approved)
    for p in problems:
        print(p)
    if not problems:
        print(f"OK {path}")
    return 1 if problems else 0


def _pr_table(m: dict) -> str:
    rows = [f"{'#':<3}{'job':<18}{'PR':<44}{'tasks':<12}{'after':<14}{'tier':<5}presupuesto"]
    for i, j in enumerate(m["jobs"], 1):
        after = ",".join(map(str, j.get("after") or [])) or "-"
        b = j["budget"]
        rows.append(f"{i:<3}{j['id']:<18}{str(j['pr_title'])[:42]:<44}"
                    f"{','.join(map(str, j['tasks'])):<12}{after:<14}{j['tier']:<5}"
                    f"{b['minutes']} min · ${b['usd']}")
    rows.append(f"PR final: {m['final_pr_title']}")
    return "\n".join(rows)


def cmd_approve(args) -> int:
    if os.environ.get("ARK_ROLE"):
        print("ark approve: rechazado. Lo está corriendo un proceso del runner (ARK_ROLE); "
              "solo Marco aprueba planes.", file=sys.stderr)
        return 2
    path = _resolve(args.target)
    problems = validate.validate(path)
    if problems:
        print("No se puede aprobar: el plan no cumple el contrato.", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    plan = load_plan(path)
    print(_pr_table(plan.manifest))
    if sys.stdin.isatty():
        answer = input("¿Aprobar este plan? [s/N] ").strip().lower()
        if answer not in ("s", "si", "sí", "y", "yes"):
            print("No aprobado.")
            return 1
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
    print(f"Aprobado {repo}/{slug} · sha256 {sha[:12]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ark", description="Arkimedes desk (sin modelo).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="features del desk por repo")
    s.add_argument("repo", nargs="?")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("plans", help="todos los planes con su aprobación y su run")
    s.set_defaults(fn=cmd_plans)
    s = sub.add_parser("validate", help="el contrato del plan")
    s.add_argument("target", help="<repo>/<slug> o ruta a plan.md")
    s.add_argument("--approved", action="store_true", help="también chequea la aprobación")
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("approve", help="cerrar un plan (Marco confirma)")
    s.add_argument("target", help="<repo>/<slug>")
    s.set_defaults(fn=cmd_approve)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass (`51 passed`: smoke 1, plan 8, globs 8, paths 4, validate 22, cli 8).

- [ ] **Step 6: Commit**

```bash
git -C ~/.claude/arkimedes add lib/ark/desk.py lib/ark/cli.py tests/test_cli.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): ark status, plans, validate and approve"
```

---

### Task 7: `bin/ark` and `bin/ark-validate`, replacing the alias

**Files:**
- Create: `bin/ark`, `bin/ark-validate`, `tests/test_bin.py`
- Modify: `~/.zshrc:30` (remove the `ark` alias)
- Create: symlink `~/.local/bin/ark` → `~/.claude/arkimedes/bin/ark`

- [ ] **Step 1: Write the failing test**

`tests/test_bin.py`:
```python
import os
import subprocess
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def env_for(ark_home, **extra):
    env = {**os.environ, "ARK_HOME": str(ark_home)}
    env.pop("ARK_ROLE", None)
    env.update(extra)
    return env


def test_bin_ark_runs_the_cli(make_feature, ark_home):
    make_feature()
    r = subprocess.run([str(CODE / "bin/ark"), "status"], capture_output=True, text=True, env=env_for(ark_home))
    assert r.returncode == 0, r.stderr
    assert "notes-edit" in r.stdout


def test_bin_ark_validate(make_feature, ark_home):
    p = make_feature()
    r = subprocess.run([str(CODE / "bin/ark-validate"), str(p)], capture_output=True, text=True,
                       env=env_for(ark_home))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("OK")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bin.py`
Expected: FAIL — `FileNotFoundError` for `bin/ark`.

- [ ] **Step 3: Write the wrappers**

`bin/ark`:
```bash
#!/usr/bin/env bash
# ark — Arkimedes from the terminal.
#   ark [claude flags]        interactive Arkimedes (queue mode; desk mode inside ~/.claude/arkimedes)
#   ark plan [claude args]    a planning session in the desk (plain claude, desk mode)
#   ark status|plans|validate|approve ...   no-model commands (lib/ark/cli.py)
set -euo pipefail
CODE="$(cd "$(dirname "$(realpath "$0")")/.." && pwd)"
ARK_HOME="${ARK_HOME:-$HOME/.claude/arkimedes}"
case "${1:-}" in
  ""|-*) exec claude --agent arkimedes --settings "$CODE/settings.json" "$@" ;;
  plan)  shift; cd "$ARK_HOME" && exec claude "$@" ;;
esac
export PYTHONPATH="$CODE/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "$CODE/.venv/bin/python" -m ark.cli "$@"
```

`bin/ark-validate`:
```bash
#!/usr/bin/env bash
# ark-validate <repo>/<slug>|<plan.md> [--approved] — the plan contract (spec §2b).
exec "$(dirname "$(realpath "$0")")/ark" validate "$@"
```

Run: `chmod +x ~/.claude/arkimedes/bin/ark ~/.claude/arkimedes/bin/ark-validate`

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_bin.py`
Expected: `2 passed`.

- [ ] **Step 5: Replace the alias**

Edit `~/.zshrc` with the Edit tool: delete the line
```
alias ark='claude --agent arkimedes --settings ~/.claude/arkimedes/settings.json'
```
Then: `ln -sf ~/.claude/arkimedes/bin/ark ~/.local/bin/ark` (`~/.local/bin` is already on PATH, line 16 of `~/.zshrc`).

Check in a new shell: `zsh -ic 'type ark; ark status'`
Expected: `ark is /Users/marcozanger/.local/bin/ark` and `Desk vacío: todavía no hay features.`

- [ ] **Step 6: Commit**

```bash
git -C ~/.claude/arkimedes add bin tests/test_bin.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): bin/ark and ark-validate; ark keeps starting interactive Arkimedes"
```

---

### Task 8: `desk-guard.py` — approvals only via `ark approve`, desk read-only for the runner

**Files:**
- Create: `hooks/desk-guard.py`, `tests/test_desk_guard.py`
- Modify: `~/.claude/settings.json` (PreToolUse hook + ask rules)

- [ ] **Step 1: Write the failing tests**

`tests/test_desk_guard.py`:
```python
import json
import os
import subprocess
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def guard(ark_home, tool, tool_input, *, role=None, cwd="/tmp"):
    env = {**os.environ, "ARK_HOME": str(ark_home)}
    env.pop("ARK_ROLE", None)
    if role:
        env["ARK_ROLE"] = role
    r = subprocess.run(["python3", str(CODE / "hooks/desk-guard.py")],
                       input=json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": cwd}),
                       capture_output=True, text=True, env=env, check=True)
    if not r.stdout.strip():
        return "allow"
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_no_tool_writes_approvals(ark_home):
    assert guard(ark_home, "Write", {"file_path": str(ark_home / "approvals/demo/x.json")}) == "deny"


def test_desk_is_writable_interactively_but_not_by_the_runner(ark_home):
    f = {"file_path": str(ark_home / "desk/demo/x/plan.md")}
    assert guard(ark_home, "Edit", f) == "allow"
    assert guard(ark_home, "Edit", f, role="worker") == "deny"


def test_shell_writes_to_approvals_are_denied_reads_allowed(ark_home):
    a = ark_home / "approvals/demo/x.json"
    assert guard(ark_home, "Bash", {"command": f"echo {{}} > {a}"}) == "deny"
    assert guard(ark_home, "Bash", {"command": f"rm {a}"}) == "deny"
    assert guard(ark_home, "Bash", {"command": f"cat {a} 2>/dev/null"}) == "allow"


def test_relative_paths_from_the_ark_folder(ark_home):
    assert guard(ark_home, "Bash", {"command": "touch approvals/demo/x.json"}, cwd=str(ark_home)) == "deny"
    assert guard(ark_home, "Bash", {"command": "ls approvals/"}, cwd=str(ark_home)) == "allow"


def test_runner_cannot_shell_write_the_desk(ark_home):
    cmd = {"command": f"sed -i '' s/draft/closed/ {ark_home}/desk/demo/x/plan.md"}
    assert guard(ark_home, "Bash", cmd) == "allow"
    assert guard(ark_home, "Bash", cmd, role="tick") == "deny"


def test_approving_only_through_ark_approve(ark_home):
    assert guard(ark_home, "Bash", {"command": ".venv/bin/python -m ark.cli approve demo/x"}) == "deny"
    assert guard(ark_home, "Bash", {"command": "ark approve demo/x"}) == "allow"


def test_unrelated_calls_pass(ark_home):
    assert guard(ark_home, "Bash", {"command": "rm -rf build"}) == "allow"
    assert guard(ark_home, "Write", {"file_path": "/tmp/x.md"}) == "allow"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_desk_guard.py`
Expected: FAIL — `python3: can't open file '.../hooks/desk-guard.py'`.

- [ ] **Step 3: Implement `hooks/desk-guard.py`** (stdlib only: it runs in every session, outside the venv)

```python
#!/usr/bin/env python3
"""User-level PreToolUse(Write|Edit|NotebookEdit|Bash): protect the Arkimedes desk (spec §2, §7).

- approvals/ is written only by `ark approve` (a Python process, not a tool call): every tool
  write there is denied, in every session.
- desk/ is read-only for runner processes (ARK_ROLE set by ark-loop / ark-dispatch).
- Approving must go through `ark approve`, which has an ask permission rule; calling the CLI
  module directly is denied.
Shell detection is a heuristic: a command that names a protected path and looks like a write.
"""
import json
import os
import re
import sys
from pathlib import Path

HOME = Path(os.environ.get("ARK_HOME") or Path.home() / ".claude" / "arkimedes").expanduser()
WRITE = re.compile(
    r"(?<![0-9&])>(?!&)"
    r"|\b(tee|cp|mv|rm|rmdir|touch|mkdir|ln|truncate|dd|install|python3?|node|perl|ruby)\b"
    r"|\bsed\s+-\w*i")
APPROVE_BYPASS = re.compile(r"ark[./]cli(\.py)?\b.*\bapprove\b")


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.exit(0)


def under(path: Path, base: Path) -> bool:
    p, b = path.resolve(), base.resolve()
    return p == b or b in p.parents


def mentions(cmd: str, name: str, cwd: str) -> bool:
    pats = [re.escape(str(HOME / name)), re.escape(str(HOME.resolve() / name)),
            re.escape(f"arkimedes/{name}"), r"ARK_HOME\}?/" + name]
    if cwd and Path(cwd).resolve() == HOME.resolve():
        pats.append(rf"(^|[\s\"'=(])(\./)?{name}(/|[\s\"')]|$)")
    return any(re.search(p, cmd) for p in pats)


def main() -> None:
    d = json.load(sys.stdin)
    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}
    cwd = d.get("cwd") or ""
    runner = bool(os.environ.get("ARK_ROLE"))
    protected = [("approvals", True,
                  "Only `ark approve <repo>/<slug>` writes approvals/, and Marco confirms it."),
                 ("desk", runner,
                  "Runner processes (ARK_ROLE) never write desk/: plans belong to Marco's planning sessions.")]
    if tool in ("Write", "Edit", "NotebookEdit"):
        f = ti.get("file_path") or ti.get("notebook_path") or ""
        if not f:
            return
        p = Path(f).expanduser()
        if not p.is_absolute():
            p = Path(cwd or ".") / p
        for name, on, reason in protected:
            if on and under(p, HOME / name):
                deny(reason)
    elif tool == "Bash":
        cmd = ti.get("command") or ""
        if APPROVE_BYPASS.search(cmd):
            deny("Approve with `ark approve <repo>/<slug>`: it is the only way, and Marco confirms it.")
        for name, on, reason in protected:
            if on and mentions(cmd, name, cwd) and WRITE.search(cmd):
                deny(reason + " (This command names that folder and looks like a write.)")


if __name__ == "__main__":
    main()
```

Run: `chmod +x ~/.claude/arkimedes/hooks/desk-guard.py`

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_desk_guard.py`
Expected: `7 passed`.

- [ ] **Step 5: Register the hook and the ask rules in the user settings**

Write this script to the session scratchpad as `register_desk_guard.py` and run it with `python3 <path>`:
```python
import json
import shutil
import time
from pathlib import Path

f = Path.home() / ".claude" / "settings.json"
shutil.copy(f, f.with_name(f"settings.json.bak-{time.strftime('%Y%m%d%H%M%S')}"))
d = json.loads(f.read_text())

ask = d.setdefault("permissions", {}).setdefault("ask", [])
for rule in ("Bash(ark approve:*)", "Bash(*/bin/ark approve:*)"):
    if rule not in ask:
        ask.append(rule)

cmd = f'python3 "{Path.home()}/.claude/arkimedes/hooks/desk-guard.py"'
pre = d.setdefault("hooks", {}).setdefault("PreToolUse", [])
if not any(h.get("command") == cmd for e in pre for h in e.get("hooks", [])):
    pre.append({"matcher": "Write|Edit|NotebookEdit|Bash", "hooks": [{"type": "command", "command": cmd}]})

f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
print("ask:", ask)
print("PreToolUse matchers:", [e.get("matcher") for e in pre])
```
Expected output: `ask` lists both rules; matchers include `Write|Edit|NotebookEdit|Bash` twice (require-worktree and desk-guard). Run `jq . ~/.claude/settings.json >/dev/null` — must exit 0.

- [ ] **Step 6: Commit**

```bash
git -C ~/.claude/arkimedes add hooks/desk-guard.py tests/test_desk_guard.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): desk-guard — approvals only via ark approve, desk read-only for the runner"
```
(`~/.claude/settings.json` is not in this repo; the backup file is next to it.)

---

### Task 9: Desk mode — CLAUDE.md, templates, session-start overview

**Files:**
- Create: `CLAUDE.md`, `templates/spec.md`, `templates/plan.md`, `hooks/desk-start.sh`, `.claude/settings.json`, `tests/test_desk_start.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_desk_start.py`:
```python
import json
import os
import subprocess
from pathlib import Path

from ark.validate import validate

CODE = Path(__file__).resolve().parents[1]


def run_hook(ark_home, **extra):
    env = {**os.environ, "ARK_HOME": str(ark_home)}
    env.pop("ARK_ROLE", None)
    env.update(extra)
    return subprocess.run([str(CODE / "hooks/desk-start.sh")], input="{}", capture_output=True,
                          text=True, env=env)


def test_desk_start_injects_the_overview(make_feature, ark_home):
    make_feature()
    r = run_hook(ark_home)
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("# Desk mode")
    assert "notes-edit" in ctx and "ark approve" in ctx


def test_desk_start_is_silent_for_runner_processes(ark_home):
    assert run_hook(ark_home, ARK_ROLE="tick").stdout == ""


def test_the_plan_template_meets_the_contract_once_filled(make_feature):
    text = (CODE / "templates/plan.md").read_text()
    text = text.replace("repo: humanharness", "repo: demo")
    assert validate(make_feature(text, spec_status=None)) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_desk_start.py`
Expected: FAIL — hook and template missing.

- [ ] **Step 3: Write `hooks/desk-start.sh`**

```bash
#!/usr/bin/env bash
# Desk mode SessionStart (project settings of ~/.claude/arkimedes): inject the desk overview. No model.
[ -n "${ARK_ROLE:-}" ] && exit 0
cat >/dev/null
CODE="$(cd "$(dirname "$0")/.." && pwd)"
if ! ov=$("$CODE/bin/ark" status 2>&1); then ov="(ark status failed: $ov)"; fi
jq -nc --arg c "# Desk mode — overview (injected by desk-start.sh)
$ov

Commands: ark status · ark plans · ark validate <repo>/<slug> · ark approve <repo>/<slug> (Marco confirms)" \
  '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$c}}'
```
Run: `chmod +x ~/.claude/arkimedes/hooks/desk-start.sh`

`.claude/settings.json` (project settings of the folder):
```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/hooks/desk-start.sh" } ] }
    ]
  }
}
```

- [ ] **Step 4: Write `templates/plan.md`**

````md
---
status: draft            # draft | closed (closed only via `ark approve`)
repo: humanharness       # the repo's path under a workspace root
slug: notes-edit         # lowercase-dashed, unique per repo; branch ark/<slug>
spec: none               # omit when spec.md sits next to this plan · a repo-relative path · none
created: 2026-11-05
---
# Notes edit and delete

## Goal

2–4 lines: the outcome, not the steps.

## Context

Spec link, constraints, what is out of scope.

## Tasks

### T1 — notes.delete handler

Files, steps, test first, done check — as the writing-plans skill describes.

## PR plan

For people: one paragraph per PR — what goes in, what does not, why this split, merge order.

## Run manifest

```yaml
setup: [pnpm install]                 # what a fresh worktree needs
integration_gate: pnpm test           # runs on ark/<slug> before every merge
final_pr_title: "feat(notes): edit and delete notes"
jobs:
  - id: shell-ipc                     # one job = one PR = one squash-merge
    pr_title: "feat(shell): notes.delete IPC"   # also the squash commit message
    includes: notes.delete handler and its tests
    excludes: any UI change
    tasks: [T1]
    area: [packages/shell/src/notes/**]
    after: []                         # job ids merged first
    tier: T1                          # T0 haiku · T1 sonnet · T2 opus
    gate: pnpm -F shell test
    heavy: false                      # heavy gates run one at a time
    done:
      - notes.delete removes the file and emits notes.changed
    budget: { minutes: 40, usd: 3 }   # estimate; history/insights.md says what jobs cost
```
````

- [ ] **Step 5: Write `templates/spec.md`**

```md
---
status: draft          # draft | approved
repo: humanharness
slug: notes-edit
created: 2026-11-05
---
# Notes edit and delete

## Problem

Who needs what, and why now.

## Goal

The outcome; how we know it worked.

## Decisions

Each: the choice, the alternatives, why (one line each).

## Design

Components, data flow, errors. Diagrams welcome (Mermaid).

## Testing

What proves it works.

## Out of scope

## Open questions

Must be empty before `status: approved`.
```

- [ ] **Step 6: Write `CLAUDE.md`**

```md
# Arkimedes desk

You are in **desk mode**: this folder holds Marco's specs and plans for every repo under the
workspace roots (`workspace.json`). Plans are written here, never inside a repo: a worker that
reads a whole plan drifts out of its job. A separate runner picks up closed plans; you never
run them from here.

Talk to Marco in Spanish. Write every file in English.

## On start

The `desk-start.sh` hook injected an overview: per repo, each feature with its spec, plan and
run state. Greet with a one-line summary of it and ask which repo and feature to work on.
Don't re-read what the overview already shows.

## Layout

| Path | What | Who writes |
|---|---|---|
| `desk/<repo>/<slug>/spec.md` | what and why · `status: draft \| approved` | this session |
| `desk/<repo>/<slug>/plan.md` | how, split into PRs · `status: draft \| closed` | this session; `closed` only via `ark approve` |
| `desk/<repo>/<slug>/notes.md` | optional: loose notes, open questions, links | this session |
| `approvals/` | one file per approved plan (path, sha256, time) | only `ark approve` |
| `runs/`, `history/`, `repos/` | runner state | never this session |
| `templates/` | spec.md and plan.md formats, with comments | Marco |
| `specs/` | Arkimedes' own design docs, not a target repo | — |

`<repo>` is the repo's path under a workspace root (`humanharness`, `muscleapp/mobile`).
`<slug>` is lowercase-dashed and unique per repo; it becomes the branch `ark/<slug>`.

## From idea to closed plan

1. Idea → `brainstorming` skill, one question at a time.
2. `spec.md` from `templates/spec.md`. `## Open questions` must be empty before approval.
3. Spec review page in Spanish (`spec-review` skill, on a translated scratchpad copy).
4. Marco approves the spec → set `status: approved`.
5. `writing-plans` skill → `plan.md`. It reads `history/insights.md`, `history/lessons.md` and
   `repos/<repo>/analysis.md` first when they exist.
6. `ark validate <repo>/<slug>` until it prints `OK`.
7. Plan review page.
8. Marco runs `ark approve <repo>/<slug>`, or you prepare the command and he confirms the
   permission prompt. Never run it on your own initiative.

A small change can skip steps 1–4 with `spec: none` in the plan's frontmatter.

## Rules

- One feature per session. When Marco switches feature, suggest `/clear`.
- Read the target repo to design, read-only. Never edit a repo from the desk.
- Never write `approvals/`, `runs/`, `history/` or `repos/` (a hook enforces `approvals/`).
- Don't edit a closed plan unless Marco asks: any edit invalidates its approval (`ark plans`
  shows `stale`) and it has to be approved again.
- A repo that is not under a workspace root, or has no `origin`, fails validation: tell Marco.

## Commands (no model)

| Command | What |
|---|---|
| `ark status [repo]` | features per repo with spec / plan / run state |
| `ark plans` | every plan with its approval (`ok`, `stale`, `-`) and run state |
| `ark validate <repo>/<slug>` | the plan contract; `--approved` also checks the approval |
| `ark approve <repo>/<slug>` | validate, show the PR table, close the plan, write the approval — Marco confirms |
```

- [ ] **Step 7: Run to verify the tests pass**

Run: `.venv/bin/python -m pytest`
Expected: all pass (`63 passed`: the 51 above + bin 2 + desk-guard 7 + desk-start 3).

- [ ] **Step 8: Commit**

```bash
git -C ~/.claude/arkimedes add CLAUDE.md templates hooks/desk-start.sh .claude/settings.json tests/test_desk_start.py
git -C ~/.claude/arkimedes commit -q -m "feat(ark): desk mode — CLAUDE.md, spec and plan templates, session-start overview"
```

---

### Task 10: Arkimedes agent pointer and `writing-plans` for desk plans

**Files:**
- Modify: `~/.claude/agents/arkimedes.md` (new section before `## 0. Start`, line 42)
- Modify: `~/.claude/skills/writing-plans/SKILL.md` ("Save plans to" block, new "Desk plan format" section, Execution Handoff)

Neither file is under git. Before editing, copy each next to itself with a `.bak-20261105` suffix.

- [ ] **Step 1: Agent file — insert before `## 0. Start`**

```md
## Desk mode

When the working directory is `~/.claude/arkimedes/`, you are in desk mode: follow that
folder's `CLAUDE.md` (specs and plans per repo, closed with `ark approve`) instead of the queue
flow below. Ignore the queue lines the session-start hook injected; the desk overview is the
state that matters. Writing under `desk/` is allowed; `approvals/` is only written by
`ark approve`, which Marco confirms.

```

- [ ] **Step 2: `writing-plans` — replace the "Save plans to" block**

Replace:
```md
**Save plans to:** `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`
- (User preferences for plan location override this default)
```
with:
```md
**Save plans to:** `~/.claude/arkimedes/desk/<repo>/<slug>/plan.md`, next to its `spec.md` — never inside the repo (a worker that reads a whole plan drifts out of its job). `<repo>` is the repo's path under a workspace root (`humanharness`, `muscleapp/mobile`); `<slug>` is lowercase-dashed and becomes the branch `ark/<slug>`.
- Write it in the desk format (see "Desk plan format" below), starting from `~/.claude/arkimedes/templates/plan.md`.
- Only when Marco asks for an in-repo plan, or the plan is for Arkimedes itself, save where he says (default `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`), without a Run manifest.
```

- [ ] **Step 3: `writing-plans` — add a section right before `## Remember`**

````md
## Desk plan format

Before writing the Run manifest, read whichever of these exist: `~/.claude/arkimedes/history/insights.md`, `~/.claude/arkimedes/history/lessons.md`, `~/.claude/arkimedes/repos/<repo>/analysis.md`. They say how to split, which tier to start at, and what budgets turned out to be.

- Frontmatter: `status: draft`, `repo`, `slug`, `created`, and `spec:` — omit it when `spec.md` (approved) sits next to the plan; a repo-relative path for a spec already in the repo; `none` for a small change.
- `## Goal` (2–4 lines, the outcome) and `## Context` (spec link, constraints, out of scope).
- `## Tasks`: one `### T<n> — <name>` per task, written as this skill says (files, steps, test first, done check).
- `## PR plan`: for people, one paragraph per PR — what goes in, what does not, why this split, merge order.
- `## Run manifest`: one ```yaml block, for the runner. One job = one PR = one squash-merge; every task in exactly one job. Top level: `setup`, `integration_gate`, `final_pr_title`. Every job: `id` (lowercase-dashed), `pr_title`, `includes`, `excludes`, `tasks`, `area` (globs; jobs that may run at the same time must not overlap — order them with `after` if they must), `after`, `tier` (T0 haiku · T1 sonnet · T2 opus), `gate`, `heavy`, `done` (checkable items), `budget: {minutes, usd}`.
- Leave out the "For agentic workers" header line: the runner executes desk plans.
- When done, run `ark validate <repo>/<slug>` and fix every problem until it prints `OK`.
````

- [ ] **Step 4: `writing-plans` — Execution Handoff**

Insert right after the `## Execution Handoff` heading:
```md
**Desk plan:** say where it is, offer the plan review page (`spec-review`), and say that `ark approve <repo>/<slug>` closes it. Marco runs it or confirms its permission prompt; never run it on your own initiative. The two options below are for in-repo plans only.
```

- [ ] **Step 5: Check**

Run: `grep -n "Desk mode" ~/.claude/agents/arkimedes.md && grep -n "Desk plan" ~/.claude/skills/writing-plans/SKILL.md`
Expected: one hit in the agent file; three in the skill (format section, handoff, the "see" line).

- [ ] **Step 6: Commit the plan's state**

The two edited files are outside the repo; record the change:
```bash
git -C ~/.claude/arkimedes commit -q --allow-empty -m "docs(ark): agent desk-mode pointer and writing-plans desk format (files outside the repo)"
git -C ~/.claude/arkimedes push -q origin main
```

---

### Task 11: Smoke test with Marco (manual)

No code. Each check is done by Marco or with him watching; write the results at the end of this plan file under `## Smoke results`.

- [ ] **Step 1: Terminal.** In a new terminal: `ark status` → `Desk vacío…`; `ark` (no args) still opens interactive Arkimedes; exit.
- [ ] **Step 2: Desk session.** `ark plan` → the session greets with the desk overview (desk-start hook works from the folder's project settings) and asks for repo and feature.
- [ ] **Step 3: A throwaway plan.** In that session, ask Claude to copy `templates/plan.md` to `desk/humanharness/ark-smoke/plan.md` (repo `humanharness`, `spec: none`). `ark validate humanharness/ark-smoke` → `OK`.
- [ ] **Step 4: The ask rule.** Ask Claude to run `ark approve humanharness/ark-smoke`. A permission prompt must appear, also in auto mode, and also from the phone via Remote Control. **Decline it.** Then ask Claude to write `approvals/humanharness/ark-smoke.json` directly → desk-guard denies it.
- [ ] **Step 5: Marco approves by hand.** Marco runs `ark approve humanharness/ark-smoke` in his terminal → PR table, `¿Aprobar este plan? [s/N]`, `s` → `Aprobado …`. `ark plans` shows `closed ok -`.
- [ ] **Step 6: Clean up.** Marco removes the smoke files by hand: `rm -r ~/.claude/arkimedes/desk/humanharness/ark-smoke ~/.claude/arkimedes/approvals/humanharness/ark-smoke.json`. Commit the smoke results and push.

---

## Self-review against the spec

| Spec item | Task |
|---|---|
| §2 plans outside repos, in `desk/<repo>/<slug>/` | 9 (CLAUDE.md, templates), 10 (writing-plans) |
| §2 plan status in frontmatter, run status in run file, `ark plans` with both | 2, 6 |
| §2 `ark approve`: validate, PR table, close, approval with sha256 + time; ask rule; refuses with ARK_ROLE | 6, 8 |
| §2 sha changes → not runnable | 5 (`--approved`), 6 (`stale`); the re-approve question is P3 |
| §2a desk mode: CLAUDE.md, templates, desk-start overview, agent pointer, `ark plan` | 7, 9, 10 |
| §2b plan structure and checks 1–6 | 2, 3, 5 |
| §7 runner processes cannot write `desk/` or `approvals/` | 8 |
| §11 rows for `bin/ark`, `ark-validate`, `desk-start.sh`, `CLAUDE.md`/`templates/`, user settings ask rule, agent file, writing-plans | 6–10 |
| §12 tests: draft never runnable, approve refuses incomplete manifest and ARK_ROLE, asks Marco in a planning session (also Remote Control, auto mode), interactive Arkimedes writes desk, `ark-validate` refuses each broken contract | 5, 6, 8, 11 |

Left for later plans: `workspace.json` limits and `tick_minutes` (P2), `usd_per_day` (P3), `templates/squad.yaml` and squad defaults for `gate`/`setup` (P5), `read-guard.sh` (P2, registered in worker settings), `ark add`/`answer`/`ps`/… (P3, P5).

## Smoke results

2026-11-05, run by Marco in a fresh terminal. All six steps passed.

| Step | Result |
|---|---|
| 1. Terminal: `type ark`, `ark status`, `ark` opens interactive Arkimedes | OK |
| 2. Desk session: `ark plan` greets with the desk overview | OK |
| 3. Throwaway plan `humanharness/ark-smoke` validates | OK |
| 4. Approve from Claude prompts and is declined; direct write to `approvals/` denied by desk-guard | OK |
| 5. Marco approves by hand; `ark plans` shows `closed ok -` | OK |
| 6. Smoke files removed | OK (verified: no `desk/humanharness/ark-smoke`, no approval file) |

Added during the smoke: the status line says `arkimedes` (agent) and `arkimedes · desk` (desk folder), 9c8de0f.
