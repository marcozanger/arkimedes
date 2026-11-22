# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
One user: Marco, a solo developer who plans features and lets an autonomous runner build them. He keeps `ark ui` open as a pinned tab beside his terminals and glances at it between other work. Each glance asks: does anything need me now, and are the runs healthy?

## Product Purpose
Arkimedes is a personal autonomous coding runner. Marco writes a plan with the `/ark-plan` skill and approves it, and the runner splits it into jobs. Claude workers execute the jobs in isolated worktrees, pass gates, and open PRs. Euklides, an AI decider, makes judgement calls during runs, and Marco reviews those calls later. `ark ui` is the local control center. In it Marco:
- sees what is waiting on him (drafts to review, plans to approve, questions from runs, stale approvals);
- acts on it;
- watches live runs;
- reviews history, spend and Euklides' decisions.

Success: within one glance Marco knows whether he is needed, and he can act without hunting for anything.

## Positioning
Runs are graphs of jobs over time. The "constellation", which shows jobs as nodes joined in order with live activity and a forecast, is how Arkimedes shows work, and it is the product's own visual mechanism.

## Operating Context
- The page sits next to terminals that run `claude` sessions and `ark` commands. Every page action has a terminal equivalent (`ark approve`, `ark answer`, `ark review`, …).
- The data comes from the runner's state on disk, polled through `/api/runs` and other JSON endpoints.
- The vocabulary is fixed: plan, spec, draft, job, run, gate, regate, approval (ok, stale), Euklides verdicts (fixed, ask, regate), beat (loop heartbeat), budget, and USD as API-equivalent spend.

## Capabilities and Constraints
- Local only: the server binds 127.0.0.1. Each write needs a session token.
- Single user. There are no accounts or roles.
- The page is a single file, `ui/index.html`, written in vanilla JS and CSS with no build step. Its markdown renderers are vendored in `ui/vendor/`.
- Desktop browser first. It must stay usable in a half-screen window (about 800px).
- UI copy is in English.
- The page writes only reviews, spec status, plan approvals, run answers and decision verdicts.

## Brand Commitments
- Dark only. The UI is always dark and does not follow the system's light mode. (Marco, 2026-11-21.)
- The names Arkimedes and Euklides.
- The constellation is the visual signature, and every screen should read as a view of the same graph: History shows past constellations, drafts are unlit nodes, and Euklides decisions are annotations on nodes. (Marco chose "todo constelación", 2026-11-21.)

## Evidence on Hand
- Real runner data comes from `ark ui` (`/api/runs`, drafts, history, Euklides).
- `ui/sample.json` is the fixture for tests.
- There is no logo or other brand asset yet.

## Product Principles
1. Show what needs Marco first. What is running and what happened come after.
2. Every piece of information has one place, and that is the only place it appears.
3. The denser a section, the more presentation it gets so it can be absorbed. Here "presentation" means ordering, grouping, scale and legibility, never decoration.
4. Clean screens over a pile of data. If something cannot be ordered, it does not belong on the screen.
5. It is a serious tool. The constellation is a way to read work, not a costume.

## Accessibility & Inclusion
- Text meets WCAG AA contrast on the dark ground.
- Motion respects `prefers-reduced-motion`.
- Every action can be done from the keyboard.
- Live status regions are announced.
