# Arkimedes

This repo is Arkimedes' code: the runner (`bin/ark-loop`, `lib/ark/`), the no-model `ark`
commands, the hooks, the worker role files (`agents/`) and the `ark-plan` skill
(`skills/ark-plan/`, linked into `~/.claude/skills/ark-plan`). The same folder is also the
runner's data home: `desk/`, `approvals/`, `runs/`, `history/`, `repos/` (git-ignored except
`desk/`).

Talk to Marco in Spanish. Write every file in English.

## Planning

Planning for the runner is the `/ark-plan` skill, from any session; its rules live in
`skills/ark-plan/SKILL.md`. Never write `approvals/`, `runs/`, `history/` or `repos/` from a
session (a hook enforces `approvals/`), and only Marco approves plans, from a terminal or with a click in `ark ui`; a session never approves on its own.

## Developing Arkimedes

- Tests: `.venv/bin/python -m pytest -q` (setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`).
- `templates/` holds the spec.md and plan.md formats; `ark validate` is the plan contract.
- `specs/` holds Arkimedes' own design docs.

## Commands (no model)

| Command | What |
|---|---|
| `ark status [repo]` | features per repo with spec / plan / run state |
| `ark plans` | every plan with its approval (`ok`, `stale`, `-`) and run state |
| `ark validate <repo>/<slug>` | the plan contract; `--approved` also checks the approval |
| `ark estimate <repo>/<slug>` | budget per job, totals, end-to-end time, plan sha256; `--json` |
| `ark approve <repo>/<slug>` | validate, show the PR table, close the plan, write the approval — Marco confirms |
| `ark report <repo>/<slug> [--open]` | a run's report.html and steps.jsonl; a closed run also goes to `history/` (`--all`: every closed run) |
| `ark review [<repo>/<slug>] [--all]` | review Euklides' decisions; new verdicts are distilled into history/euklides/lessons.md |
| `ark stats` | rewrite `history/insights.md` from `history/jobs.jsonl` |

Over running plans, also without a model: `ark ps`, `ark logs`, `ark stop`, `ark retry`, `ark abort`, `ark steer`, `ark attach`, `ark answer`.
See `docs/runner-control.md` and `docs/report.md`.
