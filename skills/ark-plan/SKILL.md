---
name: ark-plan
description: Plans work for the Arkimedes runner from any session — picks the repo and slug, decides spec + plan or plan only by the size of the ask, writes desk/<repo>/<slug>/plan.md, validates it, makes a Spanish review page with the budget and the end-to-end time, and prepares ark approve for Marco to confirm. Use when Marco says "/ark-plan", "/ark-plan <ask>", "planificá esto para el runner", "armá el plan de X", or picks a queue item to plan for ark-loop. Not for Jira tickets run by /ticket2 — that is /planear.
---

# /ark-plan — a plan for the runner, from any session

`ark-loop` runs closed plans by itself. This skill gets a plan closed: it writes files only under
`~/.claude/arkimedes/desk/`, and it never runs the plan.

Talk to Marco in Spanish. Write every file in English.

## Rules

- Read the target repo to design, read-only. Never edit it from this skill.
- Never write `~/.claude/arkimedes/approvals/`, `runs/`, `history/` or `repos/`. Only
  `ark approve` writes `approvals/`; a hook enforces it.
- Don't edit a closed plan unless Marco asks: any edit invalidates its approval (`ark plans`
  shows `stale`) and it has to be approved again.
- One feature per run of this skill. When Marco moves to another feature, suggest `/clear`.
- Never run `ark approve` on your own initiative.
- A plan belongs to one repo. An ask that spans repos gets one spec and one plan per repo.
- A repo that is not under a workspace root (`~/.claude/arkimedes/workspace.json`), or has no
  `origin`, fails validation: tell Marco.

## Writing in layers

- **Spec layers:** problem → goal → behaviour seen from outside (what the user sees, flows) →
  decisions. No files, functions or commands; a technical choice enters only as a decision.
- **Plan layers:** goal and context → PR plan (what each PR delivers, in order) → decisions →
  tasks, grouped by PR and folded (`<!-- fold -->` as the first line of `## Tasks`, one level-3
  `<job-id> — <PR title>` heading per job, its tasks at level 4). Code only inside tasks.
- **Decision format:** the chosen option in bold, then `Alternatives:`, `Pros:`, `Cons:`, `Why:`
  in that order. It is for choices a reasonable person could dispute (a new technology or
  library, a file format or contract such as CLI, hooks or API, where code lives when there is
  more than one sensible place, anything against the spec or a repo pattern) and anything costly
  to undo (dependencies, data migrations, schema changes, deleting files or commands).
  Everything else is the worker's call and is not listed.
- **Visual changes:** a before/after image pair in the spec (alt text starting `Before:` and
  `After:`, images next to spec.md), with a link to the mockup Artifact right after it.
- `ark validate` enforces the plan's layers while the plan is not closed.

## Steps

1. **Entry.** `/ark-plan` with no arguments: run `ark status`, show it, and ask what to plan.
   With an ask: go on.
2. **Repo and slug.**
   - The repo is the one the ask names, or else the session's: the main checkout is
     `dirname "$(git rev-parse --path-format=absolute --git-common-dir)"` (this also works from a
     linked worktree), and `<repo>` is its path under a root in `workspace.json`
     (`humanharness`, `muscleapp/mobile`). Outside any repo, ask.
   - Propose a slug: lowercase-dashed, unique in that repo; it becomes the branch `ark/<slug>`.
   - Show `<repo>/<slug>` in one line before writing anything. If `desk/<repo>/<slug>/` exists,
     offer to resume it or pick another slug.
     Read `review.json` when present: every section marked `change` and its note is a requested
     change; apply those first and say which.
   - When `desk/<repo>/<slug>/` holds a closed plan Marco wants to change, run
     `ark replan <repo>/<slug>` first (never edit a closed plan by hand), then edit and approve as
     usual. If the spec changes, set it to `draft` and review it again before the plan.
3. **Size.** Spec + plan when any of these hold: open product decisions (what it does, for whom,
   how it looks); it touches more than one repo; the plan will have more than 3 PRs; it changes a
   contract others use (file formats, CLI, hooks). Otherwise plan only. Say which and why in one
   line; Marco can override.
4. **Spec (spec + plan only).**
   - `brainstorming` skill, one question at a time.
   - Write `desk/<repo>/<slug>/spec.md` from `~/.claude/arkimedes/templates/spec.md`.
     `## Open questions` must be empty before approval.
   - Review page: copy the spec to the scratchpad, translate its prose to Spanish, and run the
     `spec-review` skill on the copy, passing `--base desk/<repo>/<slug>` to its render step: the
     translated copy sits in the scratchpad and the images do not.
   - Marco approves in chat → set `status: approved`.
5. **Plan.** `writing-plans` skill → `desk/<repo>/<slug>/plan.md`, from
   `~/.claude/arkimedes/templates/plan.md`. It reads `history/insights.md`, `history/lessons.md`
   and `repos/<repo>/analysis.md` first when they exist. Plan only: `spec: none` in the
   frontmatter, and a short what-and-why in `## Context`. Run `ark validate <repo>/<slug>` and fix
   every problem until it prints `OK`. The template's task layout (grouped by PR) wins over the
   flat `T<n>` headings the writing-plans skill describes.
   When the repo has `bin/ark-test` (Arkimedes' own), the manifest uses `gate: bin/ark-test job` per job and
   `integration_gate: bin/ark-test full`.
6. **Plan review page.** Copy the plan to the scratchpad and translate its prose to Spanish. Run
   `ark estimate <repo>/<slug>` and add its output to the copy, right before `## Tasks` (after
   `## Decisions`), under the heading `## Presupuesto`: the table per job, the totals, the end-to-end time and the plan's
   sha256. Then run the `spec-review` skill on the copy.
7. **Approval.** When Marco approves the review, prepare `ark approve <repo>/<slug>` and run it as
   its own whole command, exactly that (or `~/.local/bin/ark approve <repo>/<slug>`): no
   `bash -c`, no env prefix, no pipe, nothing chained. The hook denies any other form. Marco
   confirms the permission prompt. For a new version the approval shows the per-job change table. Then say that `ark-loop` picks it up, and stop.

## Commands (no model)

| Command | What |
|---|---|
| `ark status [repo]` | features per repo with spec / plan / run state |
| `ark plans` | every plan with its approval (`ok`, `stale`, `-`) and run state |
| `ark validate <repo>/<slug>` | the plan contract; `--approved` also checks the approval |
| `ark estimate <repo>/<slug>` | budget per job, totals, end-to-end time, plan sha256; `--json` |
| `ark replan <repo>/<slug>` | reopen an approved plan as the draft of its next version; the run keeps going |
| `ark diff <repo>/<slug> [vA vB]` | what changed between two plan versions, spec first |
| `ark approve <repo>/<slug>` | validate, show the PR table, close the plan, write the approval — Marco confirms |
