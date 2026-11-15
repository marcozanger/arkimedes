"""The plan contract (spec §2b), checks 1–7. A script, no model.

1 frontmatter + repo · spec · sections · 2 tasks · 3 job fields · 4 after · 5 areas · 6 approval · 7 layers (not closed)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import accounts, paths
from .globs import areas_overlap
from .plan import PlanError, load_plan, sha256_file, split_frontmatter, task_groups

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


def _non_empty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate(path, *, approved: bool = False) -> list[Problem]:
    path = Path(path)
    out: list[Problem] = []

    def add(check: str, message: str) -> None:
        out.append(Problem(check, message))

    try:
        plan = load_plan(path)
    except PlanError as e:
        return [Problem("frontmatter", str(e))]
    except UnicodeDecodeError as e:
        return [Problem("frontmatter", f"cannot read {path}: not valid UTF-8 ({e.reason})")]
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
    if fm.get("status") != "closed":
        _check_layers(plan, add)
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
    if not loc or path.name != "plan.md":
        add("frontmatter", "plan is not on the desk (expected desk/<repo>/<slug>/plan.md)")
    else:
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
    else:
        try:
            accounts.check(str(fm["repo"]))
        except accounts.AccountError as e:
            add("account", str(e))
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
        except (PlanError, OSError, UnicodeDecodeError):
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


MERGE_KEYS = ("tier", "budget", "against", "overlap")


def _check_merge(m, add):
    if "merge" not in m:
        return
    g = m["merge"]
    if not isinstance(g, dict):
        return add("manifest", "`merge` must be a mapping")
    for k in g:
        if k not in MERGE_KEYS:
            add("manifest", f"merge: unknown key `{k}`")
    for k in ("tier", "budget"):
        if _missing(g, k):
            add("manifest", f"merge: missing `{k}`")
    if not _missing(g, "tier") and (not isinstance(g["tier"], str) or g["tier"] not in TIERS):
        add("manifest", f"merge: tier must be T0, T1 or T2, not {g['tier']!r}")
    b = g.get("budget")
    if not _missing(g, "budget") and not (isinstance(b, dict) and _positive(b.get("minutes"))
                                          and _positive(b.get("usd"))):
        add("manifest", "merge: budget needs positive numeric `minutes` and `usd`")
    for k in ("against", "overlap"):
        if k in g and not _str_list(g[k]):
            add("manifest", f"merge: `{k}` must be a list of non-empty strings")


def _check_manifest(m, tasks, add):
    for key in ("integration_gate", "final_pr_title"):
        if m.get(key) in (None, "", [], {}):
            add("manifest", f"missing `{key}`")
        elif not _non_empty_str(m[key]):
            add("manifest", f"`{key}` must be a non-empty string")
    if "setup" in m and not (isinstance(m["setup"], list) and all(_non_empty_str(c) for c in m["setup"])):
        add("manifest", "`setup` must be a list of non-empty strings (commands)")
    _check_merge(m, add)
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


def _missing(j, key) -> bool:
    v = j.get(key)
    return key not in j or v is None or (isinstance(v, (str, list, dict)) and not v)


def _check_job(jid, j, add):
    for key in JOB_FIELDS:
        if _missing(j, key):
            add("jobs", f"{jid}: missing `{key}`")
    for key in ("pr_title", "includes", "excludes", "gate"):
        if not _missing(j, key) and not _non_empty_str(j[key]):
            add("jobs", f"{jid}: `{key}` must be a non-empty string")
    if not _missing(j, "tier") and (not isinstance(j["tier"], str) or j["tier"] not in TIERS):
        add("jobs", f"{jid}: tier must be T0, T1 or T2, not {j['tier']!r}")
    for key in ("area", "done"):
        if not _missing(j, key) and not _str_list(j[key]):
            add("jobs", f"{jid}: `{key}` must be a list of non-empty strings")
    if not _missing(j, "budget"):
        b = j["budget"]
        if not (isinstance(b, dict) and _positive(b.get("minutes")) and _positive(b.get("usd"))):
            add("jobs", f"{jid}: budget needs positive numeric `minutes` and `usd`")
    if "heavy" in j and not isinstance(j["heavy"], bool):
        add("jobs", f"{jid}: `heavy` must be true or false")
    if "companions" in j and not isinstance(j["companions"], bool):
        add("jobs", f"{jid}: `companions` must be true or false")
    if "after" in j and j["after"] is not None and not isinstance(j["after"], list):
        add("jobs", f"{jid}: `after` must be a list")
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


LAYER_ORDER = ("Goal", "Context", "PR plan", "Decisions", "Tasks", "Run manifest")
CARD_LABELS = ("Alternatives:", "Pros:", "Cons:", "Why:")
CARD_START = re.compile(r"^- \*\*([^*\n]+)\*\*")
FOLD = "<!-- fold -->"


def _check_layers(plan, add):
    """The layer contract of a plan that is not closed yet (spec review-layers)."""
    secs = plan.sections
    if not secs.get("Decisions"):
        add("layers", "missing or empty section `## Decisions` (write `None.` and why when no "
                      "choice needs the full format)")
    present = [n for n in secs if n in LAYER_ORDER]
    if present != [n for n in LAYER_ORDER if n in present]:
        add("layers", "sections must come in the order " + " → ".join(f"`## {n}`" for n in LAYER_ORDER))
    for item in _list_items(secs.get("Decisions", "")):
        problem = _card_problem(item)
        if problem:
            m = CARD_START.match(item)
            name = m.group(1).strip() if m else item[2:42].strip()
            add("layers", f"decision {name!r}: {problem}")
    _check_task_groups(plan, add)


def _list_items(text: str) -> list[str]:
    """Top-level `- ` items, each with its indented or blank continuation lines."""
    items: list[list[str]] = []
    cur: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("- "):
            cur = [line]
            items.append(cur)
        elif cur is not None and (not line.strip() or line[:1] in (" ", "\t")):
            cur.append(line)
        else:
            cur = None
    return ["\n".join(i).strip() for i in items]


def _card_problem(item: str) -> str | None:
    if not CARD_START.match(item):
        return "must start with the chosen option in bold"
    flat = " ".join(item.split())
    pos = 0
    for label in CARD_LABELS:
        i = flat.find(label, pos)
        if i < 0:
            return (f"needs {', '.join(f'`{x}`' for x in CARD_LABELS)} in that order; "
                    f"`{label}` is missing or out of order")
        pos = i + len(label)
    return None


def _check_task_groups(plan, add):
    section = plan.sections.get("Tasks", "")
    if not section.lstrip().startswith(FOLD):
        add("layers", f"`## Tasks` must start with a `{FOLD}` line")
    flat = [b.id for b in plan.blocks if b.level == 3]
    if flat:
        add("layers", f"tasks {', '.join(flat)} are level-3 headings; put each as a level-4 "
                      "`#### T<n>` under its PR's `### <job-id>` heading")
    loose = [b.id for b in plan.blocks if b.level == 4 and b.group is None]
    if loose:
        add("layers", f"tasks {', '.join(loose)} are not under a `### <job-id>` heading")
    if plan.manifest is None:
        return
    jobs = {str(j.get("id")): j for j in plan.manifest.get("jobs") or [] if isinstance(j, dict)}
    seen: set[str] = set()
    for g in task_groups(section):
        if g in seen:
            add("layers", f"`### {g}` appears twice under `## Tasks`")
            continue
        seen.add(g)
        if g not in jobs:
            add("layers", f"`### {g}` under `## Tasks` is not a job id in the Run manifest")
            continue
        under = [b.id for b in plan.blocks if b.group == g]
        want = [str(t) for t in jobs[g].get("tasks") or []] if isinstance(jobs[g].get("tasks"), list) else []
        if sorted(under) != sorted(want):
            add("layers", f"group `{g}` holds {', '.join(under) or 'no tasks'} "
                          f"but its job lists {', '.join(want) or 'none'}")


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
    if not isinstance(rec, dict):
        add("approval", "approval file unreadable: not a JSON object")
        return
    if rec.get("plan") != str(path.resolve()):
        add("approval", f"approval record is for another plan ({rec.get('plan')})")
    if rec.get("sha256") != sha256_file(path):
        add("approval", "the plan changed after approval; run `ark approve` again")
