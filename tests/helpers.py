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
