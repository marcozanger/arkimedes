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


DECISION = ("- **Shell first.** Alternatives: UI first. Pros: the UI has an API to call. "
            "Cons: nothing to see until PR 2. Why: the UI needs the handler.")


def _task(t, level):
    return f"{'#' * level} {t} — step\n\nDo it, test first."


def _grouped_tasks(m, tasks):
    """`<!-- fold -->`, then one `### <job-id>` per job holding its tasks as level 4; tasks of no
    job go under a `### loose` group, so plans built to test task problems still parse."""
    jobs = m.get("jobs") if isinstance(m, dict) else None
    parts, owned = ["<!-- fold -->"], []
    for j in jobs if isinstance(jobs, list) else []:
        if not isinstance(j, dict) or not isinstance(j.get("tasks"), list):
            continue
        mine = [t for t in j["tasks"] if t in tasks and t not in owned]
        if not mine:
            continue
        owned += mine
        parts.append(f"### {j.get('id')} — {j.get('pr_title', '')}")
        parts += [_task(t, 4) for t in mine]
    rest = [t for t in tasks if t not in owned]
    if rest:
        parts.append("### loose — tasks in no job")
        parts += [_task(t, 4) for t in rest]
    return "\n\n".join(parts)


def plan_text(m=None, *, tasks=("T1", "T2", "T3"), drop=(), flat=False, **fm):
    """A complete desk plan in the layered shape; `flat=True` gives the old shape (no Decisions,
    `### T<n>` tasks, PR plan after the tasks). Frontmatter keys passed as None are left out."""
    front = {"status": "draft", "repo": "demo", "slug": "notes-edit", "created": "2026-11-05"}
    front.update(fm)
    front = {k: v for k, v in front.items() if v is not None}
    m = m if m is not None else manifest()
    run = "```yaml\n" + yaml.safe_dump(m, sort_keys=False) + "```"
    if flat:
        sections = {
            "Goal": "Edit and delete notes.",
            "Context": "Spec next to this plan.",
            "Tasks": "\n\n".join(_task(t, 3) for t in tasks),
            "PR plan": "Shell first, then the UI.",
            "Run manifest": run,
        }
    else:
        sections = {
            "Goal": "Edit and delete notes.",
            "Context": "Spec next to this plan.",
            "PR plan": "Shell first, then the UI.",
            "Decisions": DECISION,
            "Tasks": _grouped_tasks(m, list(tasks)),
            "Run manifest": run,
        }
    body = "\n\n".join(f"## {k}\n\n{v}" for k, v in sections.items() if k not in drop)
    return "---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n# Notes edit\n\n" + body + "\n"


import json
from pathlib import Path


def approve(path, repo="demo", slug="notes-edit", at="2026-11-05T10:00:00+00:00"):
    """What the approve command leaves behind, without the terminal prompt."""
    from ark import paths
    from ark.plan import set_status, sha256_file
    set_status(path, "closed")
    f = paths.approval_file(repo, slug)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"repo": repo, "slug": slug, "plan": str(Path(path).resolve()),
                             "sha256": sha256_file(path), "approved_at": at}))
    return f


def set_accounts(ark_home, rules):
    """Put `accounts` rules into the test workspace.json the ark_home fixture wrote."""
    import json
    f = ark_home / "workspace.json"
    ws = json.loads(f.read_text())
    ws["accounts"] = rules
    f.write_text(json.dumps(ws))


def no_load_brake(ark_home):
    """A gate waits while the machine is busy; a test that waits for a run must not depend on how busy it is."""
    import json
    f = ark_home / "workspace.json"
    ws = json.loads(f.read_text())
    ws["limits"] = {**ws.get("limits", {}), "load_max": 0}
    f.write_text(json.dumps(ws))
