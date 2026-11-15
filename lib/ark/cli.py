"""`ark` subcommands that need no model: status, plans, validate, estimate, approve, report, stats, runs, and the run control ones.

`ark` with no arguments is handled by bin/ark (it starts Claude); planning is the /ark-plan skill.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import approve, ctl, desk, estimate, forge, monitor, paths, reporting, review, sweep, validate
from .plan import load_plan, sha256_file
from .report import minutes, usd


def _resolve(target: str) -> Path:
    p = Path(target).expanduser()
    if p.suffix == ".md":
        return p
    repo, _, slug = target.rstrip("/").rpartition("/")
    if not repo or not slug:
        raise SystemExit(f"ark: '{target}' is neither <repo>/<slug> nor the path of a plan.md")
    return paths.desk_dir() / repo / slug / "plan.md"


def cmd_status(args) -> int:
    fs = [f for f in desk.features() if not args.repo or f.repo == args.repo]
    if not fs:
        print(f"No features for {args.repo}." if args.repo else "Desk empty: no features yet.")
        return 0
    repo = None
    for f in fs:
        if f.repo != repo:
            repo = f.repo
            print(repo)
        print(f"  {f.slug:<28} spec {f.spec:<9} plan {f.plan:<7} status {f.run:<8} elapsed {f.elapsed:<6} eta {f.eta}")
    return 0


def cmd_plans(args) -> int:
    fs = [f for f in desk.features() if f.plan != "-"]
    if not fs:
        print("No plans in the desk.")
        return 0
    print(f"{'repo/slug':<40} {'plan':<7} {'approval':<11} run")
    for f in fs:
        print(f"{f.repo + '/' + f.slug:<40} {f.plan:<7} {f.approval:<11} {f.run}")
    return 0


def cmd_validate(args) -> int:
    path = _resolve(args.target)
    problems = validate.validate(path, approved=args.approved)
    for p in problems:
        print(p)
    if not problems:
        print(f"OK {path}")
    return 1 if problems else 0


def _pr_table(m: dict) -> str:
    rows = [f"{'#':<3}{'job':<18}{'PR':<44}{'tasks':<12}{'after':<14}{'tier':<5}budget"]
    for i, j in enumerate(m["jobs"], 1):
        after = ",".join(map(str, j.get("after") or [])) or "-"
        b = j["budget"]
        rows.append(f"{i:<3}{j['id']:<18}{str(j['pr_title'])[:42]:<44}"
                    f"{','.join(map(str, j['tasks'])):<12}{after:<14}{j['tier']:<5}"
                    f"{b['minutes']} min · ${b['usd']}")
    rows.append(f"Final PR: {m['final_pr_title']}")
    return "\n".join(rows)


def cmd_approve(args) -> int:
    if "ARK_ROLE" in os.environ:
        print("ark approve: refused. A runner process is running it (ARK_ROLE); "
              "only Marco approves plans.", file=sys.stderr)
        return 2
    interactive = sys.stdin.isatty()
    if not interactive and os.environ.get("CLAUDECODE") != "1":
        print("ark approve: without a terminal a plan can only be approved from a Claude Code session "
              "(its permission prompt is the confirmation); run it from a terminal.", file=sys.stderr)
        return 2
    path = _resolve(args.target)
    try:
        sha_seen = sha256_file(path)
    except OSError as e:
        print(f"ark approve: could not read {path}: {e}", file=sys.stderr)
        return 1
    problems = approve.check(path)
    if problems:
        print("Cannot approve: the plan does not meet the contract.", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    plan = load_plan(path)
    print(_pr_table(plan.manifest))
    if interactive:
        answer = input("Approve this plan? [y/N] ").strip().lower()
        if answer not in ("s", "si", "sí", "y", "yes"):
            print("Not approved.")
            return 1
    try:
        rec = approve.approve_plan(path, expect_sha=sha_seen)
    except approve.ApproveError as e:
        if e.kind == "changed":
            print("The plan changed while it was being approved; run ark approve again.", file=sys.stderr)
        else:
            print(f"ark approve: {e}", file=sys.stderr)
        return 1
    print(f"Approved {rec['repo']}/{rec['slug']} · sha256 {rec['sha256'][:12]}")
    return 0


def cmd_estimate(args) -> int:
    path = _resolve(args.target)
    problems = validate.validate(path)
    if problems:
        for p in problems:
            print(p)
        return 1
    plan = load_plan(path)
    writers = int(paths.load_workspace()["limits"]["writers_per_repo"])
    e = estimate.estimate(plan.manifest, writers)
    e["sha256"] = sha256_file(path)
    if args.json:
        print(json.dumps(e, indent=2))
        return 0
    print("| job | tier | after | minutes | USD |")
    print("|---|---|---|---|---|")
    for j in e["jobs"]:
        print(f"| {j['id']} | {j['tier']} | {', '.join(j['after']) or '–'} | {minutes(j['minutes'])} | {usd(j['usd'])} |")
    if e["merge"]:
        g = e["merge"]
        print(f"| merge | {g['tier']} | – | {minutes(g['minutes'])} | {usd(g['usd'])} |")
    print(f"| **total** | | | {minutes(e['total_minutes'])} | {usd(e['total_usd'])} |")
    print()
    print(f"End to end: {minutes(e['end_to_end_minutes'])} "
          f"(up to {writers} jobs at once, each with its own budgeted minutes).")
    print(f"Plan sha256: {e['sha256']}")
    return 0


def _bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if n < 10 or unit == "KB" else f"{n:.1f} {unit}"


def _shown(item) -> str:
    """A path under the worktrees folder as wt/<repo>/<slug>/<job>; anything else as it is."""
    root = paths.worktrees_dir()
    try:
        return "wt/" + Path(item.target).relative_to(root).as_posix()
    except ValueError:
        return item.target


def cmd_sweep(args) -> int:
    if not args.dry_run:
        try:
            ctl._writer()
        except ctl.CtlError as e:
            print(e, file=sys.stderr)
            return 1
    try:
        now = datetime.now(timezone.utc)
        f = forge.get()
        plans, problems = sweep.plan(now, f)
    except (ValueError, OSError, forge.ForgeError) as e:
        print(f"ark sweep: {e}", file=sys.stderr)
        return 1
    for p in problems:
        print(f"warning: {p}", file=sys.stderr)
    if not plans:
        print("Nothing to sweep.")
        return 0
    res = None if args.dry_run else sweep.apply(plans, f, now)
    removed = set(res["removed"]) if res else set()
    errors = dict(res["errors"]) if res else {}

    def mark(*items) -> str:
        """The outcome of a line made of one or more items, in a real sweep ("" in a dry run)."""
        if res is None:
            return ""
        bad = [errors[i.key()] for i in items if i.key() in errors]
        if bad:
            return f"  error: {bad[0]}"
        return "  removed" if all(i.key() in removed for i in items) else "  skipped"

    size = nwt = npr = 0
    names: set[tuple[str, str, str]] = set()
    nheld = 0
    for rp in plans:
        print(f"{rp.repo}/{rp.slug} · {rp.why}" if rp.repo else f"leftovers · {rp.why}")
        branches: dict[str, list] = {}
        for i in rp.items:
            if i.kind in ("local_branch", "remote_branch"):
                branches.setdefault(i.target, []).append(i)
        for i in rp.items:
            if i.kind == "worktree":
                print(f"  worktree  {_shown(i)}  ({_bytes(i.size)}){mark(i)}")
                if not res or i.key() in removed:
                    size += i.size
                    nwt += 1
            elif i.kind == "pr":
                print(f"  pr        {i.target}  (close){mark(i)}")
                npr += not res or i.key() in removed
            elif i.kind == "folder":
                print(f"  folder    {_shown(i)}{mark(i)}")
        for b, items in branches.items():
            where = ", ".join("local" if i.kind == "local_branch" else "origin" for i in items)
            print(f"  branch    {b}  ({where}){mark(*items)}")
            names.add((rp.repo, rp.slug, b))
        for i, why in rp.held:
            print(f"  held      {_shown(i) if i.kind in ('worktree', 'folder') else i.target}: {why}")
            nheld += 1
    verb = "would free" if args.dry_run else "freed"
    nbr = len(names) if args.dry_run else len({n for n in names if any(
        k in removed for k in (f"local_branch:{n[0]}/{n[1]}:{n[2]}", f"remote_branch:{n[0]}/{n[1]}:{n[2]}"))})
    print(f"{verb} {_bytes(size)} · {nwt} worktrees · {nbr} branches · {npr} PRs · {nheld} held")
    return 1 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ark", description="Arkimedes desk and run control (no model).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="desk features per repo")
    s.add_argument("repo", nargs="?")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("plans", help="every plan with its approval and its run")
    s.set_defaults(fn=cmd_plans)
    s = sub.add_parser("validate", help="the plan contract")
    s.add_argument("target", help="<repo>/<slug> or path to plan.md")
    s.add_argument("--approved", action="store_true", help="also checks the approval")
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("approve", help="close a plan (Marco confirms)")
    s.add_argument("target", help="<repo>/<slug>")
    s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("estimate", help="a plan's budget and end-to-end time")
    s.add_argument("target", help="<repo>/<slug> or path to plan.md")
    s.add_argument("--json", action="store_true", help="the same data as JSON")
    s.set_defaults(fn=cmd_estimate)
    s = sub.add_parser("sweep", help="clear what finished and abandoned runs leave behind")
    s.add_argument("--dry-run", action="store_true", help="show what would go and why, change nothing")
    s.set_defaults(fn=cmd_sweep)
    ctl.register(sub)
    monitor.register(sub)
    reporting.register(sub)
    review.register(sub)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
