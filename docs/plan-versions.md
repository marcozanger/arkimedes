# Plan versions

Every approval freezes the plan and its spec as a numbered version (v1, v2, ...) beside the
approval. The runner runs that frozen copy, so editing the desk never holds or changes a run.

## What a version is

An approval writes `vN.plan.md` (and `vN.spec.md` when the plan has a spec) next to the approval,
and appends a line to `versions.jsonl` (version, time, plan sha256). The desk copy is only the
draft of the next version.

- The runner reads the frozen snapshot, never the desk.
- Editing `desk/<repo>/<slug>/` after approval changes nothing in a running plan until the next
  approval.

## `ark replan <repo>/<slug>`

Reopens an approved plan as the draft of its next version. The plan goes back to `draft`
(the spec too, when it has to change: set it to `draft` and review it again before the plan);
the run keeps going on the current version. An approval from before versions existed is adopted
as v1: its snapshot is stored as `v1.legacy.md`, so there is always something to diff against.

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

Shows what changed between two versions, spec first, then the plan, job by job. With no
versions it compares the latest two.

## Where versions show

- `ark plans` lists the current version next to the approval state.
- The run report has a Versions section: each version, when it was adopted and what it changed.
- The history keeps the version a run closed on; the control center shows it too.
