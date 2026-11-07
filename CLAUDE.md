# Arkimedes desk

You are in **desk mode**: this folder holds Marco's specs and plans for every repo under the
workspace roots (`workspace.json`). Plans are written here, never inside a repo: a worker that
reads a whole plan drifts out of its job. A separate runner picks up closed plans; you never
run them from here.

Talk to Marco in Spanish. Write every file in English.

## On start

The `desk-start.sh` hook injected an overview: per repo, each feature with its spec, plan and
run state. Greet with a one-line summary of it and ask which repo and feature to work on.
Don't re-read what the overview already shows.

## Layout

| Path | What | Who writes |
|---|---|---|
| `desk/<repo>/<slug>/spec.md` | what and why · `status: draft \| approved` | this session |
| `desk/<repo>/<slug>/plan.md` | how, split into PRs · `status: draft \| closed` | this session; `closed` only via `ark approve` |
| `desk/<repo>/<slug>/notes.md` | optional: loose notes, open questions, links | this session |
| `approvals/` | one file per approved plan (path, sha256, time) | only `ark approve` |
| `runs/`, `history/`, `repos/` | runner state | never this session |
| `templates/` | spec.md and plan.md formats, with comments | Marco |
| `specs/` | Arkimedes' own design docs, not a target repo | — |

`<repo>` is the repo's path under a workspace root (`humanharness`, `muscleapp/mobile`).
`<slug>` is lowercase-dashed and unique per repo; it becomes the branch `ark/<slug>`.
The plan must sit at exactly `desk/<repo>/<slug>/plan.md`; `ark validate` rejects it elsewhere.

## From idea to closed plan

1. Idea → `brainstorming` skill, one question at a time.
2. `spec.md` from `templates/spec.md`. `## Open questions` must be empty before approval.
3. Spec review page in Spanish (`spec-review` skill, on a translated scratchpad copy).
4. Marco approves the spec → set `status: approved`.
5. `writing-plans` skill → `plan.md`. It reads `history/insights.md`, `history/lessons.md` and
   `repos/<repo>/analysis.md` first when they exist.
6. `ark validate <repo>/<slug>` until it prints `OK`.
7. Plan review page.
8. Marco runs `ark approve <repo>/<slug>`, or you prepare the command and he confirms the
   permission prompt. Never run it on your own initiative. Run it as its own whole command,
   exactly `ark approve <repo>/<slug>` (or `~/.local/bin/ark approve <repo>/<slug>`): the
   desk-guard hook denies any other form (`bash -c`, relative `bin/ark`, env prefixes, pipes,
   chained commands).

A small change can skip steps 1–4 with `spec: none` in the plan's frontmatter.

## Rules

- One feature per session. When Marco switches feature, suggest `/clear`.
- Read the target repo to design, read-only. Never edit a repo from the desk.
- Never write `approvals/`, `runs/`, `history/` or `repos/` (a hook enforces `approvals/`).
- Don't edit a closed plan unless Marco asks: any edit invalidates its approval (`ark plans`
  shows `stale`) and it has to be approved again.
- A repo that is not under a workspace root, or has no `origin`, fails validation: tell Marco.
- In the run manifest, `gate`, `pr_title`, `includes`, `excludes`, `integration_gate` and
  `final_pr_title` are non-empty strings and `setup` is a list of strings; validation fails
  otherwise.

## Commands (no model)

| Command | What |
|---|---|
| `ark status [repo]` | features per repo with spec / plan / run state |
| `ark plans` | every plan with its approval (`ok`, `stale`, `-`) and run state |
| `ark validate <repo>/<slug>` | the plan contract; `--approved` also checks the approval |
| `ark approve <repo>/<slug>` | validate, show the PR table, close the plan, write the approval — Marco confirms |

Over running plans, also without a model: `ark ps`, `ark logs`, `ark stop`, `ark steer`, `ark attach`, `ark answer`.
See `docs/runner-control.md`.
