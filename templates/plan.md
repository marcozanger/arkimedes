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
