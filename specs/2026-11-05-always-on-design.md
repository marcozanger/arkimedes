# Arkimedes always-on — design

Date: 2026-11-05 · Status: approved by Marco (2026-11-05)
Scope: `~/.claude/agents/arkimedes.md`, `~/.claude/arkimedes/` (hooks, settings, bin), the `writing-plans` skill. Works for every repo under the watched workspace roots, not only Human Harness.

"Default branch" below means each repo's `origin/HEAD` (`master` or `main`).

## 1. Goal

Marco spends the day on specs and plans across his repos. When he approves a plan
(`ark approve`), an always-on Arkimedes notices it, runs it with worker
agents, and merges the results into a per-plan integration branch. Marco only
tests that branch and merges it to the default branch. A short decisions file
explains what was decided along the way. Many repos run in parallel.

Non-goals: Arkimedes never merges to the default branch; it does not run the coach
queue in this mode (the queue keeps the current daytime Arkimedes flow); no cloud runner.

## 2. Day side: plans live outside the repo, Marco approves them

- **Plans never live in a repo.** A worker reading a whole plan gets confused and
  drifts out of its job, and plans are not reading material for people browsing the
  repo. Plans live in `~/.claude/arkimedes/desk/<repo>/<slug>/plan.md`, next to
  their spec (§2a).
  `writing-plans` writes there instead of `docs/`. New specs live there too; specs
  already in a repo (e.g. HH's `docs/superpowers/specs/`) stay and a plan can link them.
- Only the orchestrator reads plans. Workers and reviewers get only their brief,
  which Arkimedes cuts from the plan (goal, area, gate, done criteria of their job);
  `read-guard.sh` denies them any read under `desk/` and `approvals/` (§6).
- **Two separate pieces.** *Planning* is interactive: Marco works plans in his own
  Claude session. *Running* is the runner (`ark-loop`, §4): a script that every
  `tick_minutes` picks up closed plans and works them headless. They share only the
  `desk/` and `approvals/` folders; neither starts or talks to the other.
- **Planning session.** Any interactive session Marco opens for it: a plain `claude`
  or the interactive Arkimedes (`ark`), usually in `~/.claude/arkimedes/`
  (`planner` is a shortcut that opens one there). He can drive it from the computer
  or from his phone (Remote Control) and manages its context himself (one plan per
  session, `/clear` when he wants), so planning stays cheap. The folder's `CLAUDE.md`
  tells any session there how to work (§2a). Interactive sessions may write under `desk/`;
  the runner's processes (`ARK_ROLE` set by `ark-loop`/`ark-dispatch`) may not.
- Plans can be reviewed with the spec-review page, as today.
- **Plan status** lives in the plan's frontmatter: `status: draft` while being worked,
  `status: closed` once approved. Run status (`running`, `done`, `blocked`) lives in
  the run file, never in the plan, so the plan's hash stays stable. `ark plans` lists
  every plan with both statuses.
- **Approval = `ark approve <repo>/<plan-slug>`.** It runs `ark-validate` (the plan
  contract, §2b), shows the PR table, sets `status: closed`, and writes
  `~/.claude/arkimedes/approvals/<repo>/<plan-slug>.json` with the plan path, its
  sha256 and the approval time. Marco runs it himself, or the planning session runs
  it as an **ask** permission rule (in the user settings, so it holds in any session),
  so Claude can prepare it but Marco confirms every approval (a tap on the phone). It
  refuses to run when `ARK_ROLE` is set, and no runner process can write under
  `approvals/` or `desk/` (§7), so the runner cannot approve its own work.
- If the plan file changes after approval, its sha no longer matches: the run holds,
  the merge guard refuses further merges, and Marco gets a question to re-approve.
- A plan is runnable only when it meets the **plan contract** (§2b).
- When a run starts, Arkimedes records the approval file, the plan sha256 and the
  default-branch commit it branched from in the run file.

## 2a. The Arkimedes folder (desk mode)

When Marco opens a session in `~/.claude/arkimedes/` (plain `claude`, `ark`, or
`planner` from anywhere), it is in **desk mode**: it already knows the layout, the
formats and the steps from idea to a closed plan, with no extra prompt.

**Layout**

```
~/.claude/arkimedes/
  CLAUDE.md            desk mode: layout, lifecycle, formats, rules (below)
  workspace.json       roots, limits, caps (§3)
  templates/           spec.md, plan.md, squad.yaml — the formats, with comments
  desk/                Marco's side; runner processes cannot read it (§6)
    <repo>/<slug>/       one folder per feature
      spec.md              what and why          status: draft | approved
      plan.md              how, split into PRs   status: draft | closed
      notes.md             optional: loose notes, open questions, links
  approvals/<repo>/<slug>.json    written only by ark approve
  repos/<repo>/        state.json, analysis.md (onboarding, §3a)
  runs/<repo>/<slug>/  run file, steps.jsonl, receipts, reports, logs, report.html
  history/             jobs.jsonl, plans.jsonl, insights.md, lessons.md (§9a)
  questions.md         open questions for Marco (§8)
  agents/ hooks/ bin/  Arkimedes' own agents, guards and scripts
  settings.json        the runner's settings
  specs/               Arkimedes' own design docs (this file), not a target repo
```

**Lifecycle of a feature folder** (`ark status` shows it per repo):

| State | Where it lives | Who moves it |
|---|---|---|
| spec draft → approved | `spec.md` frontmatter | Marco says so in the session; Claude sets it |
| plan draft → closed | `plan.md` frontmatter | `ark approve`, confirmed by Marco |
| running → done / blocked | run file | the runner |

A plan needs an approved spec, or `spec: none` in its frontmatter for small changes.

**Spec format** (`templates/spec.md`), English, short sections:

```md
---
status: draft          # draft | approved
repo: humanharness
slug: notes-edit
created: 2026-11-05
---
# Notes edit and delete
## Problem        — who needs what, and why now
## Goal           — the outcome; how we know it worked
## Decisions      — each: the choice, the alternatives, why (one line each)
## Design         — components, data flow, errors; diagrams welcome (Mermaid)
## Testing        — what proves it works
## Out of scope
## Open questions — must be empty before status: approved
```

The plan format is §2b.

**What `CLAUDE.md` tells the session**
- On start, the `desk-start.sh` hook injects a short overview (a script, no model):
  per registered repo, the feature folders with their state, open questions, plans
  running. The session greets with that and asks which repo and feature.
- Steps: idea → brainstorming, one question at a time → `spec.md` → spec-review page
  in Spanish → `approved` → `writing-plans` (reading `history/insights.md`,
  `lessons.md` and `repos/<repo>/analysis.md` first) → `plan.md` → plan review page →
  Marco runs or confirms `ark approve`.
- Rules: one feature per session, suggest `/clear` when switching feature; files in
  English, chat in Spanish; read the target repo only (read-only) to design, never
  edit it from the desk; never write `runs/`, `approvals/`, `history/` or `repos/`;
  never run `ark approve` on its own; new repo → suggest `ark add` first.
- Commands it can use: `ark status`, `ark plans`, `ark add`, `ark answer` (Marco
  answers), `ark approve` (ask rule).

**Arkimedes' agent file** gets a short "desk mode" section: when the working directory
is `~/.claude/arkimedes/`, follow `CLAUDE.md` there instead of the queue flow.

## 2b. Plan contract

The plan says not only what to build but how it is split into PRs and merges. The
contract is what `ark-loop` checks before it starts anything, and what every merge is
checked against afterwards.

**Structure** (one Markdown file):

```md
---
status: draft            # draft | closed (closed only via ark approve)
repo: humanharness       # folder name under a workspace root
slug: notes-edit         # unique per repo; branch ark/<slug>
spec: docs/superpowers/specs/2026-11-05-notes-edit-design.md   # optional
created: 2026-11-05
---
# Notes edit and delete
## Goal          — 2–4 lines: the outcome, not the steps
## Context       — spec link, constraints, what is out of scope
## Tasks         — numbered T1…Tn: files, steps, test first, done check
## PR plan       — for people: one paragraph per PR (what goes in, what not,
                   why this split, merge order)
## Run manifest  — for the runner: one fenced yaml block (below)
```

**Run manifest** (machine-readable; the PR plan says the same in prose):

```yaml
setup: [pnpm install]                 # what a fresh worktree needs
integration_gate: pnpm test           # run on ark/<slug> after every merge
final_pr_title: "feat(notes): edit and delete notes"
jobs:
  - id: shell-ipc                     # one job = one PR = one squash-merge
    pr_title: "feat(shell): notes.delete IPC"   # also the squash commit message
    includes: notes.delete handler and its tests
    excludes: any UI change
    tasks: [T1, T2]
    area: [packages/shell/src/notes/**]
    after: []                         # job ids merged first
    tier: T1                          # starting tier
    gate: pnpm -F shell test
    heavy: false                      # heavy gates obey the §3 limit
    done:
      - notes.delete removes the file and emits notes.changed
    budget: { minutes: 40, usd: 3 }   # estimate, from history (§9a)
```

**Ready to start**: `ark-validate` (a script, no model) checks all of these. `ark approve`
refuses a plan that fails any; the pre-check runs it again before a new run.
1. Frontmatter complete; `repo` is a git repo with `origin` under a workspace root;
   `slug` unique in that repo.
2. Every task is in exactly one job; every job has at least one task.
3. Every job has `pr_title`, `includes`, `excludes`, `area`, `gate`, `tier`, and at least
   one `done` item.
4. `after` names existing jobs and has no cycle.
5. Jobs that may run at the same time (no `after` path between them) have
   non-overlapping `area`s.
6. `status: closed`, an approval file exists, and the plan's sha256 matches it.

At start time the pre-check adds the runtime conditions: fetch of the repo works, no
area overlap with work in flight in that repo, limits allow. Anything that fails →
one question (§8), the plan holds.

**Each PR honors its job.** The worker gets the job as its brief; the PR body is
generated from it (tasks, includes, excludes, done); the merge guard checks the diff
against `area` (§7); the reviewer checks it against `includes` / `excludes` / `done`
and marks anything outside as a finding. A job that needs to change its contract
(more area, a split, a new job) stops and asks; Arkimedes may only narrow scope on its
own, and records it as a decision (§9). `writing-plans` learns to write this format.

## 3. Workspace: many repos

- `~/.claude/arkimedes/workspace.json`:

  ```json
  {
    "roots": ["~/Documents/workspace"],
    "exclude": [],
    "limits": { "writers_per_repo": 3, "writers_total": 6, "heavy_gates": 1 },
    "tick_minutes": 15
  }
  ```
- The pre-check walks every direct subfolder of each root that is a git repo with an
  `origin`, and one level deeper for subfolders that are not repos (e.g.
  `muscleapp/mobile`, `muscleapp/backend`), skipping `exclude` and linked worktrees, and `git fetch`es it (to branch from a fresh default
  branch). Only approved plans run, so watching all repos is safe.
- Plan detection costs no tokens: list `approvals/<repo>/*.json` with no run file yet
  and check each plan's sha256 — no model.
- Everything is scoped per repo: runs in `runs/<repo>/<plan-slug>.md` (+ folder),
  branches `ark/<plan-slug>` in that repo, the decisions file in that repo.
- Collision rules (area overlap, hotspots, work in flight in other worktrees/sessions)
  apply within a repo only. Different repos always run in parallel.
- Limits (defaults above, tunable): writers per repo 3, writers in total 6
  (CPU/RAM and the Claude plan's rate limits), heavy gates at once 1 (two Electron
  e2e suites at once flake). Jobs over a limit wait; order = oldest plan first, then
  `after` order within the plan.
- Per-repo setup for workers: `ark-dispatch` reads the repo's `CLAUDE.md`, runs the
  manifest's `setup`, and writes the worktree's `.claude/settings.local.json` with the
  push permission (today it is copied by hand into each worktree).

## 3a. Squad per repo

Each repo has its own development process (a squad): who implements, who reviews,
what else runs (QA, design), which gate, which conventions. Arkimedes respects it and
only builds one where there is none. Its guards always apply on top.

**The squad file** lives in the repo, versioned: `.claude/squad.yaml`.

```yaml
version: 1
setup: [flutter pub get]                # default for plans; a plan may add more
gate: scripts/gate.sh                   # default job gate
integration_gate: scripts/gate.sh --full
conventions: [CLAUDE.md, DESIGN.md]     # passed in every brief
steps:                                  # run in order for every job
  - { role: dev,    agent: dev }        # the repo's own agents (.claude/agents or plugins)
  - { role: review, agent: reviewer, verdict: required }   # CLEAN/FIX tied to the head SHA
  - { role: qa,     agent: qa, when: "area matches lib/ui/**" }
tracker: none                           # none | jira (see below)
model: tier                             # tier: Arkimedes' tier wins; agent: the agent's own model
```

- **Using it.** For each job `ark-dispatch` runs the steps in order, each as
  `claude -p --agent <agent>` in the job's worktree. The dev step is the worker of §6;
  every step with `verdict: required` must return CLEAN for the head SHA before the
  merge guard allows a merge. Plans may omit `gate`/`setup` per job; they default to
  the squad's.
- **Guards cannot be relaxed by a squad.** area-guard, read-guard, merge-guard,
  done-gate, budgets and the no-default-branch rule apply to every step whatever the
  repo's agents allow.
- **External tools.** `tracker: none` → tools that change outside systems (Jira
  transitions, comments) are denied in runner processes. `tracker: jira` → allowed only
  for a job whose manifest names a ticket.
- **Pinned per run.** The run records the squad file's sha at start; a squad change on
  the default branch applies to the next run, not the current one.

**Registering a repo = onboarding.** A repo is registered with `ark add <path>` (or
automatically when the first plan for it is closed). Repos found under the roots but
not registered are only listed by `ark-loop` ("2 repos sin registrar"); nothing runs
for them, so the existing folders do not trigger a wave of analyses. Registration
starts an `onboard` tick (opus), once per repo:
1. **Situation analysis.** It reads `CLAUDE.md` and other docs, `.claude/agents`,
   `.claude/skills`, enabled plugins and their agents, `.claude/settings*.json` hooks,
   package scripts, Makefile, `scripts/gate*`, CI workflows, git hooks, test folders,
   stack and size, and recent git history (who commits, PR habits). It writes
   `~/.claude/arkimedes/repos/<repo>/analysis.md`: stack, how to build and test, how
   long the gate takes (it runs it once), what process already exists, gaps and risks.
2. **Squad.** If `.claude/squad.yaml` exists, it checks it against the analysis
   (agents and gate exist and work) and only reports problems. If something like a
   squad exists without the file (local agents such as dev/reviewer/qa, a gate script,
   a ticket skill), it maps that into a squad file and keeps it. Missing roles are filled
   with Arkimedes' defaults (`ark-dev`, `ark-reviewer` in `~/.claude/arkimedes/agents/`).
   Only agents about development, review or testing are mapped; domain agents (e.g. a
   ghost-writer) are not; anything ambiguous becomes a question.
3. **Proposal.** The squad file goes as a PR to the default branch with the analysis
   summary in its body, and Marco gets a phone notification.

Repo state lives in `~/.claude/arkimedes/repos/<repo>/state.json`: `onboarding` →
`proposed` (PR open) → `ready` (squad on the default branch). A closed plan for a repo
that is not `ready` holds with one question: merge the squad PR, or run this plan with
the proposal as is. `ark add --again <repo>` repeats the analysis (e.g. after a big
change to the repo's tooling).

**Getting better over time.** History rows (§9a) carry the repo and step, so
`insights.md` has a section per repo (e.g. review rounds per job, what QA catches, gate
time). After a run, the close tick may propose squad changes backed by that data
("reviews in this repo find UI issues in 4 of 10 jobs → add a QA step for `lib/ui/**`";
"gate takes 12 min → a fast gate per job, the full one for integration"). Each proposal
is a PR to the default branch touching only `.claude/squad.yaml` and the repo's agent
files, with the evidence in its body, at most one open squad PR per repo. Nothing
changes until Marco merges it, so the process never edits itself, and it cannot weaken
its own review.

## 4. The loop

The always-on part is a shell script, not a Claude session. Claude only runs in
short, fresh, headless ticks, so its context never accumulates (see §10).

- Marco keeps one terminal open running `ark-loop` (`~/.claude/arkimedes/bin/ark-loop`).
- Every `tick_minutes`, `ark-loop`:
  1. Takes a lock; if a previous cycle is still running, skips.
  2. **Pre-check, no model** (per repo, §3): approved plans with no run file; approved
     plans whose sha changed; active runs with new receipts, pending merges or new answers from
     `ark answer`; worker anomalies (§6).
  3. Nothing to do → sleep. No tokens spent.
  4. Work found → one tick per run that has work, **in sequence**, each a fresh
     `claude -p --agent arkimedes --model <m> --max-turns <n> --output-format json "tick: <kind> <repo> <run>"`.
     More repos means more ticks, never a longer session.
- What a tick does (one step for its run, then exit):
  - **new plan:** the pre-check already ran `ark-validate` and the runtime
    conditions (§2b). Met → create the run file and integration branch, dispatch
    the first ready jobs within limits. Not met → write one question (§8), hold.
  - **receipts:** read worker/reviewer receipts, call `ark-merge` for clean jobs,
    dispatch reviewers, escalations and newly unblocked jobs.
  - **intervene:** handle a worker anomaly (§6).
  - **close:** open the final PR, write the decisions file header, notify.
- Workers cannot be Agent-tool subagents (they would die with the tick). The tick calls
  `ark-dispatch start <repo> <run> <job> <tier>`, which starts a detached `claude -p`
  worker in its own worktree with the brief; on exit it writes the receipt file in the
  run folder, and the next pre-check sees it. Reviewers are dispatched the same way.
- **Instant start.** `ark-loop` also watches `approvals/` and the run folders' receipt
  files (`fswatch`); a change wakes it at once instead of waiting for the next tick.
  The `tick_minutes` cycle stays as the fallback (and catches stalls and timeouts).
- **Startup checks.** `ark-loop` refuses to start if `ANTHROPIC_API_KEY` is set (it
  would bill per token behind Marco's back) and unsets it for every child process.
- Restart: `ark-loop` is stateless; everything lives in run files and `.proc` files.

## 5. Running a job

- At run start Arkimedes creates `ark/<plan-slug>` from the default branch and pushes it.
- Each job: worker in an isolated worktree based on the current `ark/<plan-slug>`
  (so its `after` jobs are already in), branch `ark/<plan-slug>-<job>`,
  runs the job's gate, writes its report (including a `DECISIONS` section, §9).
- **Every** job gets a reviewer (the squad's review step, §3a; another agent; T1, T2
  when risky), whatever the diff size, plus any other steps the squad defines.
  The reviewer writes a verdict tied to the head SHA.
- Clean + gate green → PR against `ark/<plan-slug>`, squash-merge (via `ark-merge`).
  No GitHub approval: PRs are authored with Marco's own `gh` account, which cannot
  approve its own PR. The reviewer's verdict file for the head SHA is the approval.
- Escalation as today: two failures move a tier up; failing at T2 → job `[!]`, its
  dependents hold, the rest continues.
- **Integration gate, before the merge:** `ark-integrate` builds a temporary merge of
  the current `ark/<plan-slug>` plus the PR head in a scratch worktree and runs the
  plan's `integration_gate` there. Green → `ark-merge` squash-merges (only if
  `ark/<plan-slug>` has not moved since; otherwise check again). Red → no merge, the job
  goes back to its worker once with the failure, then `[!]` and its dependents hold. The
  integration branch only ever receives merges that passed, so there are no reverts.
  (Gates are local; HH has no PR CI.)
- When every job is merged or blocked: open the PR `ark/<plan-slug>` → default branch,
  with the decisions file linked at the top, and notify Marco.

## 6. Worker control

Workers are separate `claude -p` processes. Each has a `session_id`, so it can be
resumed with its full context: `claude -p --resume <session_id> "<message>"`.

**Prevent (hooks in every worker).** `ark-dispatch` starts each worker with its own
settings and `ARK_ROLE=worker`:
- `area-guard.sh`: Write/Edit outside the job's `area` is denied; the worker must report it.
- `merge-guard.sh`: a worker never merges or approves; only the orchestrator does.
- `read-guard.sh`: Read/Grep/Glob and Bash reads under `~/.claude/arkimedes/desk/`
  and `approvals/` are denied; the worker works from its brief only.
- `done-gate.sh` (Stop hook): when the worker tries to finish, it runs the job's `gate`
  and blocks the exit with the failure output while it is red, so the worker fixes it
  in the same session. After 3 blocks it lets the worker end `FAILED` with the last
  output; only then do the §5 escalation rules count an attempt.
- `--max-turns` per tier: a hard cap, so no worker runs forever.

**Observe (no model, in the pre-check).** `ark-dispatch` runs the worker with
`--output-format stream-json` to a log and writes `runs/<repo>/<run>/<job>.proc`:
PID, session_id, start time, tier, attempt. The pre-check flags:

| Anomaly | Detection |
|---|---|
| Died without receipt | PID gone, no receipt |
| Stalled | log unchanged for 20 min |
| Over time | over the tier's max (T0 20 min, T1 60 min, T2 120 min) |
| Over tokens | cumulative usage in the stream over the tier's budget |

A flag triggers an **intervene** tick that receives only the reason, the worker's last
message and the log path — never the whole log. Limits are starting points, tuned from `log.jsonl`.

**Act (Arkimedes decides in the intervene tick).**
- **Wait:** a long gate that is making progress → extend the limit once.
- **Steer:** `ark-dispatch steer <job> "<instruction>"` kills the process if alive and
  resumes it with `--resume` and the instruction ("stop here, only fix test X",
  "that is out of area, revert it"). Cheaper than restarting: the context is kept.
- **Answer:** a worker that ended `BLOCKED` with a question → Arkimedes answers it if
  the plan covers it and resumes with `--resume`; otherwise the question goes to Marco
  (`ark answer`), and the worker is resumed with his answer.
- **Stop and escalate:** `ark-dispatch stop <job>`, then a new worker one tier up with
  the brief plus the previous report path. The worktree is kept.
- **Stop and block:** already failed at T2 → `[!]` and notify.
- Stale `[>]` job without a receipt after a restart: commits on its branch → review; none → re-dispatch.

Every intervention goes to the run file, and to the decisions file if it changed the plan.

**Marco can intervene too.**
- Everything runs headless: one terminal shows `ark-loop`; workers and reviewers are
  background processes with no window.
- `ark ps`: live workers with repo, job, elapsed time, tokens, last message.
- `ark logs <job>`: follow a worker's stream-json log, rendered readable; read-only.
- `ark stop <job>`: stop a worker.
- `ark attach <job>`: opens `claude --resume <session_id>` **interactive** in the
  worker's worktree. Marco takes over; when he exits, Arkimedes treats the job as a
  finished worker and sends it to review.

## 7. Guards (hooks, not prompt rules)

`merge-guard.sh` (PreToolUse on Bash, matches `gh pr merge` / `gh pr review --approve`;
`ark-merge` runs the same checks). Allows only when all hold:
- the caller is the orchestrator (`ARK_ROLE` is not `worker`);
- the PR's base is `ark/<plan-slug>` of an active run, never the default branch;
- the PR's head branch is `ark/<plan-slug>-<job>` for a job in that run's manifest;
- the plan file still has the sha256 in its approval file;
- every changed file (`gh pr diff --name-only`) matches the job's `area`;
- a CLEAN review verdict and a green gate result exist for the PR's current head SHA.

Denial → logged in the run file and a critical notification (§8).

No runner process (`ARK_ROLE` set: ticks, workers, reviewers) may write under
`desk/` or `approvals/`: `guard-edits.sh` denies Write/Edit there and a Bash guard
denies shell writes to those paths. Writers are only interactive planning sessions
(plans) and `ark approve` confirmed by Marco (approvals).

Kept: `require-model.sh`, `guard-edits.sh` (runs/ and decisions drafts writable,
nothing else for the orchestrator), no push to master/main, no force-push, no deleting
branches with unmerged work, no main checkout, prod or secrets. Settings: auto mode with
an allowlist for git, gh, package managers and gate commands. Limit: hooks stop
mistakes, not a model actively working around them; enough for this use.

## 8. Questions and notifications

- **Blocking for one plan/job** (incomplete manifest, collision, job failed at T2,
  worker question outside the plan): that plan/job holds with one concrete question in
  `~/.claude/arkimedes/questions.md` (shown by `ark-loop` too); everything else keeps
  going. Marco runs `ark answer` in any terminal: it shows pending questions one by
  one, with repo and plan, and stores each answer. The next pre-check sees it and
  triggers a tick; the answer is recorded as a decision (§9).
- **Critical → phone (PushNotification):** merge guard denied something; integration
  gate still red on a PR after its retry; spending cap hit; `gh`/model auth expired or disk full; nothing left to advance;
  a plan finished and its PR is ready.
- **Not critical → summary only:** worker doubts, out-of-scope findings (parked in the
  coach queue as today), tier escalations, interventions that worked.
- **Spending cap.** The pre-check sums `usd` in the run's `steps.jsonl`. Over the sum
  of the plan's job budgets → the plan holds (running workers finish, nothing new is
  dispatched) with a question: raise the cap or stop. A daily cap across all plans in
  `workspace.json` (`limits.usd_per_day`) holds everything the same way and notifies the phone.
- Arkimedes never waits idle on an answer.
- `ark-loop` shows a status line per cycle, e.g. `HH 2 · cockpit 1 · ❓1`.

## 9. Decisions file

- One per plan: `docs/decisions/<plan-slug>.md` in the plan's repo, committed on
  `ark/<plan-slug>`, reaching the default branch with the final PR.
- Single writer: Arkimedes. Workers and reviewers propose candidates in a
  `DECISIONS` section of their report; Arkimedes keeps only key ones and adds its
  own and Marco's (every `ark answer`).
- Key = something a human reviewer would ask about: a choice between real
  alternatives; a deviation from or interpretation of the plan; an assumption filling
  a gap; a job blocked, failed integration, escalated or steered and why; something deferred to
  the queue. Not routine implementation detail. Target about 1–2 per job, not a hard cap.
- Format: header (plan, dates, jobs merged/blocked, decisions by author), then
  one section per job; each decision is one visible line of 1–2 sentences with its
  author, and the detail folded in `<details>`:

  ```md
  - **D7** · Note order lives in the manifest, not a separate file. · _worker (sonnet)_
    <details><summary>más</summary>

    Why: the plan left it open; the manifest is already written atomically.
    Rejected: notes-order.json (one more write to keep in sync).
    See: PR #141 · report `runs/…/job3.md`
    </details>
  ```
- Language follows the repo's docs (English in HH).

## 9a. Run report and history

Every plan ends with a report of what happened and what it cost, and that data feeds
the next plans.

**Capture (no model).** Every Claude process (tick, worker, reviewer) runs with JSON
output; its final result carries session_id, model, duration, turns, token usage
(input, output, cache read, cache write) and cost. `ark-loop`, `ark-dispatch`,
`ark-merge` and `ark-integrate` append one row per step to
`runs/<repo>/<slug>/steps.jsonl`:

```json
{"step":17,"kind":"worker","job":"shell-ipc","attempt":2,"tier":"T1","model":"sonnet",
 "start":"…","end":"…","tokens":{"in":…,"out":…,"cache_read":…,"cache_write":…},
 "usd":1.42,"outcome":"done","reason":"gate red on attempt 1"}
```

Kinds: each tick kind, worker, reviewer, gate, merge, integrate, intervene,
and `wait-marco` (time a plan or job was held on a question). Scripts write rows with
duration and no tokens. On a subscription the USD is the API-equivalent estimate the
CLI reports; the report labels it so.

**Report.** At close, `ark-report` (a script, no model) renders
`runs/<repo>/<slug>/report.html` from the run file, `steps.jsonl` and the decisions file:
- Summary: wall time, working time, time waiting for Marco, total tokens and USD,
  PRs merged / blocked, integration failures, estimate vs actual.
- Decisions: the decisions file, one line each with "más".
- Per PR: tier path, attempts, steers, review rounds, gate failures, minutes, tokens, USD.
- Timeline: every step in order with its duration and outcome.
- Agents: tokens, USD and time by role (tick, worker, reviewer) and by model.
Delivery: always the local file; then the close tick publishes the same HTML as a
private claude.ai Artifact (one per plan) and the "plan finished" phone notification
carries its link. If publishing fails (headless, auth), only the local file remains and
the notification says so. The final PR to the default branch links the report.

**History.** At close, the same script appends one row per job to
`~/.claude/arkimedes/history/jobs.jsonl` (repo, plan, job, tasks, area size, files and
lines changed, start and final tier, attempts, steers, review rounds, gate failures,
minutes, tokens, USD, budget, outcome) and one row per plan to `history/plans.jsonl`.

**Feeding the next plans (small, never raw logs).**
- `ark-stats` (script) recomputes `history/insights.md`, at most 30 lines: success
  rate by starting tier per repo, median cost and time by job size, estimate error,
  most common failure reasons.
- The close tick adds at most 3 one-line lessons to `history/lessons.md` (e.g. "HH jobs
  with e2e gates: start at T2"). Marco can edit or prune it.
- Who reads it: the planning session (the desk `CLAUDE.md` tells it to read both before
  writing a manifest: split, tier, budget); the new-plan tick (both injected, ≤60 lines);
  `ark-loop` takes the over-time and over-token limits (§6) from the per-tier p90 in
  history once there are 10 or more jobs for that tier.

## 10. Context and token economy

- **Fresh session per tick.** No session lives longer than one step; there is never
  anything to `/clear` or compact. Run files are the only memory.
- **Idle cycles are free.** The shell pre-check decides whether Claude runs at all.
- **Mechanics are scripts.** Merging, the pre-merge integration gate, the final PR,
  guard checks and worker start/stop/steer are `ark-merge` / `ark-integrate` /
  `ark-dispatch`; the tick calls them and reads `OK` / `FAIL <reason>`. Tokens go only
  to judgment: lanes, briefs, escalations, interventions, filtering decisions.
- **Small step, hard cap.** One step per run per tick, `--max-turns` per kind. Hitting
  the cap → save state, exit; the next tick continues.
- **Minimal input.** In tick mode the session-start hook injects only the tick's run,
  one line per job (status, SHA, report path), plus the pending answer or anomaly if
  any. No queue, no log, no other repos. Full reports are opened only on FAILED/BLOCKED.
- **Model per tick, chosen by the pre-check:**

  | Tick kind | Model |
  |---|---|
  | receipts, dispatch of already-planned jobs, close | sonnet |
  | new plan (lanes, briefs), escalation after a failure, intervene, answer that changes a plan | opus |

- **Measure.** Every step's usage goes to `steps.jsonl` and, at close, to `history/`
  (§9a); tiers, caps and limits are tuned from that data.

## 11. Changes by file

| File | Change |
|---|---|
| `~/.claude/agents/arkimedes.md` | New "Always-on mode" section (§3–§10) and a short "desk mode" pointer (§2a); tools add PushNotification; the current queue mode stays as is |
| `~/.claude/arkimedes/workspace.json` | New (§3) |
| `~/.claude/arkimedes/bin/ark-loop` | New: supervisor, lock, per-repo pre-check, anomaly detection, tick classification, launch |
| `~/.claude/arkimedes/bin/ark-dispatch` | New: `start` / `stop` / `steer` workers and reviewers; `.proc` files; worktree setup and settings |
| `~/.claude/arkimedes/bin/ark-merge`, `ark-integrate` | New: deterministic merge, integration gate on a temporary merge; same checks as merge-guard |
| `~/.claude/arkimedes/desk/`, `approvals/` | New: specs and plans per repo and feature; approval files written only by `ark approve` |
| `~/.claude/arkimedes/CLAUDE.md`, `templates/` | New: desk mode instructions and the spec, plan and squad templates (§2a) |
| `~/.claude/arkimedes/hooks/desk-start.sh` | New: desk overview injected at session start in the folder |
| `~/.claude/settings.json` | `ark approve` as an ask permission rule, so every session asks Marco |
| `~/.claude/arkimedes/hooks/read-guard.sh` | New: workers/reviewers cannot read plans or approvals (§6) |
| `~/.claude/arkimedes/repos/<repo>/` | New: `state.json`, `analysis.md` from onboarding (§3a) |
| `~/.claude/arkimedes/bin/ark` | New: `ark add`, `ark plan`, `ark plans`, `ark approve`, `ark answer`, `ark ps`, `ark logs`, `ark stop`, `ark attach` (a tmux view is out of scope for now) |
| `~/.claude/arkimedes/bin/ark-validate` | New: plan contract checks (§2b), used by `ark approve` and the pre-check |
| `~/.claude/arkimedes/bin/ark-report`, `ark-stats` | New: `steps.jsonl` → `report.html`; history rows; `insights.md` (§9a) |
| `~/.claude/arkimedes/history/` | New: `jobs.jsonl`, `plans.jsonl`, `insights.md`, `lessons.md` |
| `<repo>/.claude/squad.yaml` | New per repo, proposed by Arkimedes via PR when missing (§3a) |
| `~/.claude/arkimedes/agents/ark-dev.md`, `ark-reviewer.md` | New: default roles for repos without their own |
| `~/.claude/arkimedes/hooks/done-gate.sh` | New: worker Stop hook that runs the gate before exit (§6) |
| `~/.claude/arkimedes/hooks/merge-guard.sh` | New (§7) |
| `~/.claude/arkimedes/hooks/area-guard.sh` | New: worker-side area enforcement (§6) |
| `~/.claude/arkimedes/hooks/guard-edits.sh` | Allow decisions drafts under runs/ |
| `~/.claude/arkimedes/settings.json`, new `worker-settings.json` | Register guards; auto-mode allowlists |
| `~/.claude/arkimedes/hooks/session-start.sh` | Tick mode: inject only the tick's run (§10) |
| `writing-plans` skill | Write plans to `~/.claude/arkimedes/desk/<repo>/<slug>/plan.md`, not the repo, in the §2b format (PR plan + yaml Run manifest); read `insights.md` and `lessons.md` first |
| memory `push-permission` | Note: Arkimedes may merge PRs into `ark/<plan-slug>` integration branches of a plan Marco approved with `ark approve`; the default branch stays Marco's |

## 12. Testing

Two sandbox repos under a test root.
- Repo A, a two-job plan (B after A), end to end: detection, both PRs merged into the
  integration branch, final PR opened, decisions file written.
- Repo B, a one-job plan at the same time: runs in parallel with A; the heavy-gate and
  writer limits hold.
- Guard cases that must be refused: a file outside the area (area-guard and merge-guard),
  base = default branch, head branch not in the manifest, plan changed after approval,
  missing review verdict for the head SHA, a worker trying to merge, a worker reading
  a plan, a runner process writing a plan or an approval file.
- An unapproved (`draft`) plan in `desk/` is never picked up; `ark approve` refuses an
  incomplete manifest and refuses to run with `ARK_ROLE` set; in a planning session
  (plain or interactive Arkimedes) it always asks Marco, also over Remote Control in
  auto mode.
- The interactive Arkimedes can write a plan under `desk/` (its guards apply only to
  runner processes).
- `ark-validate` refuses each broken contract: a task in no job or in two, a job
  without `done`, an `after` cycle, two parallel jobs with overlapping areas.
- A reviewer flags a diff that goes beyond `includes` / `excludes`.
- After the repo A run: `steps.jsonl` has a row for every tick, worker, reviewer, gate
  and merge; `report.html` totals match the rows; `history/` gets one row per job;
  `insights.md` is regenerated and stays ≤30 lines.
- Repo with a squad (dev, reviewer, qa agents and `scripts/gate.sh`): the run uses
  those agents in order and that gate; a QA step with `verdict: required` that returns
  FIX blocks the merge.
- `ark add` on a repo with local dev/reviewer agents and a gate script but no squad
  file: the onboard tick writes `analysis.md` and opens a PR whose squad maps those
  agents and that gate; a repo with nothing gets the default agents.
- Unregistered repos under a root are listed and never analysed; a closed plan for a
  repo still `proposed` holds with the question.
- A squad whose agents allow edits outside the area or merging still hits the guards.
- `tracker: none` denies a Jira transition from a runner process.
- `done-gate.sh` blocks a worker's exit while its gate is red and lets it go after 3 blocks.
- A PR whose temporary merge fails the integration gate is never merged into `ark/<plan>`.
- A plan over its budget holds and asks; `ark-loop` refuses to start with `ANTHROPIC_API_KEY` set.
- Writing an approval file wakes `ark-loop` within seconds.
- The close tick publishes the report as an Artifact from a headless `claude -p`; if
  it cannot, the notification still arrives with the local path.
- Worker control: a stalled worker is detected and steered with `--resume`; a killed
  worker without receipt is re-dispatched; `ark attach` hands a job back to review.
- An integration-gate failure on the temporary merge blocks the merge and holds the
  dependent job.
- An idle cycle launches no Claude process.
