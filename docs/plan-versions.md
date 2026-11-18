# Plan versions

Every approval freezes the plan and its spec as a numbered version (v1, v2, ...) beside the
approval. The runner runs that frozen copy, so editing the desk never holds or changes a run.

## What a version is

An approval copies the plan and its spec into `approvals/<repo>/<slug>/v<N>/plan.md` and
`v<N>/spec.md`, and appends a line to `versions.jsonl` beside them (version, plan and spec sha256,
approval time). A version is never written twice. The desk copy is only the draft of the next
version.

- The runner reads the frozen snapshot, never the desk.
- Editing `desk/<repo>/<slug>/` after approval changes nothing in a running plan until the next
  approval.

## `ark replan <repo>/<slug>`

Reopens an approved plan as the draft of its next version. The plan goes back to `draft`
(the spec too, when it has to change: set it to `draft` and review it again before the plan);
the run keeps going on the current version. An approval from before versions existed is adopted
as v1: replan keeps its text in the desk folder as `v1.legacy.md` (the spec as
`v1.legacy.spec.md`), and the next approval stores them as `v1/plan.md` and `v1/spec.md`, so there
is something to diff against. If no copy was kept, v1 has no text and `ark diff` says so.

Edit the draft, then approve as usual.

## What the approval shows

For a new version, the PR table is followed by what the version does to each job: unchanged,
changed, removed or added, and whether the job is already merged (see the rules below). Marco
confirms it like any approval.

## Adoption rules

The runner adopts a new version in any run state and applies only what changed.

| Job in the new version | Not merged yet | Already merged |
|---|---|---|
| unchanged | keeps going, untouched | stays as it is |
| changed | restarts from the new definition | a follow-up job is added; main is not rewritten |
| removed | dropped (branch and PR cleaned up) | stays in main |
| added | runs like any new job | n/a |

A follow-up job is a new job: it branches from main and opens its own PR.

For a finished run:

- with an open final PR, the run reopens on its branch and applies the changes there;
- otherwise (final PR merged or closed), it is a fresh start from main with the new version.

## `ark diff <repo>/<slug> [vA vB]`

Prints a unified diff between two versions, the spec first, then the plan. With no versions it
compares the latest two. A v1 whose text was not kept (a legacy approval replanned without a
copy) is named as such.

## Where versions show

`versions.jsonl` is the list. The approval table and `ark diff` read it; the other views (`ark plans`,
the report, the history, the control center) are not covered here.
