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

## PR plan

For people: one paragraph per PR — what it delivers, what it does not, why this split. End with the merge order. No files, functions or code here.

## Decisions

Only the technical choices a reasonable person could dispute or that are costly to undo; everything else is the worker's call. Each one in this format (write `None.` and why when there is none):

- **Move the note to a trash folder**
  - Alternatives: delete the file for good; mark it deleted in a database.
  - Pros: the user can undo a delete; no data loss on a misclick.
  - Cons: the trash folder needs its own cleanup.
  - Why: deleting by mistake is the likeliest user error, and undo is cheap here.

## Tasks

<!-- fold -->

One group per PR, its heading the job id and PR title; its tasks one level below, written as the writing-plans skill describes (files, steps, test first, done check). Subheadings inside a task use level 5.

### shell-ipc — feat(shell): notes.delete IPC

#### T1 — notes.delete handler

Files, steps, test first, done check.

## Run manifest

```yaml
setup: [pnpm install]                 # what a fresh worktree needs
integration_gate: pnpm test           # runs on ark/<slug> before every merge; Arkimedes' own plans: bin/ark-test full
final_pr_title: "feat(notes): edit and delete notes"
# merge:                                # only when the plan overlaps plans in flight (parallel-plans)
#   against: [humanharness/other-plan]  # the plans it overlaps with
#   overlap: [packages/shell/src/**]    # what they share
#   tier: T2                            # the refresh worker's model
#   budget: { minutes: 30, usd: 4 }     # spent across the run's refreshes
jobs:
  - id: shell-ipc                     # one job = one PR = one squash-merge
    pr_title: "feat(shell): notes.delete IPC"   # also the squash commit message
    includes: notes.delete handler and its tests
    excludes: any UI change
    tasks: [T1]
    area: [packages/shell/src/notes/**]
    after: []                         # job ids merged first
    tier: T1                          # T0 haiku · T1 sonnet · T2 opus
    gate: pnpm -F shell test          # Arkimedes' own plans: bin/ark-test job; gates get ARK_TEST_WORKERS (limits.gate_workers): `vitest --maxWorkers=$ARK_TEST_WORKERS`
    heavy: false                      # heavy gates still run one at a time
    companions: true                  # optional, default true: a few small test/doc files outside the area are allowed; false keeps the area strict
    done:
      - notes.delete removes the file and emits notes.changed
    budget: { minutes: 40, usd: 3 }   # estimate; history/insights.md says what jobs cost
```
