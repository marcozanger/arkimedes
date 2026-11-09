"""`ark` subcommands that need no model: status, plans, validate, estimate, approve, report, stats, and the run control ones.

`ark` with no arguments is handled by bin/ark (it starts Claude); planning is the /ark-plan skill.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import ctl, desk, estimate, paths, reporting, validate
from .plan import load_plan, set_status, sha256_file
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
    problems = validate.validate(path)
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
    if sha256_file(path) != sha_seen:
        print("The plan changed while it was being approved; run ark approve again.", file=sys.stderr)
        return 1
    set_status(path, "closed")
    sha = sha256_file(path)
    repo, slug = str(plan.frontmatter["repo"]), str(plan.frontmatter["slug"])
    f = paths.approval_file(repo, slug)
    f.parent.mkdir(parents=True, exist_ok=True)
    rec = {"repo": repo, "slug": slug, "plan": str(path.resolve()), "sha256": sha,
           "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)
    print(f"Approved {repo}/{slug} · sha256 {sha[:12]}")
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
    print(f"| **total** | | | {minutes(e['total_minutes'])} | {usd(e['total_usd'])} |")
    print()
    print(f"End to end: {minutes(e['end_to_end_minutes'])} "
          f"(up to {writers} jobs at once, each with its own budgeted minutes).")
    print(f"Plan sha256: {e['sha256']}")
    return 0


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
    ctl.register(sub)
    reporting.register(sub)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
