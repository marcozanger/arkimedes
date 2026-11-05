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
