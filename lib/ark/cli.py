"""`ark` subcommands that need no model: status, plans, validate, approve.

`ark` with no arguments is handled by bin/ark (it starts Claude); planning sessions use bin/planner.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import desk, paths, validate
from .plan import load_plan, set_status, sha256_file


def _resolve(target: str) -> Path:
    p = Path(target).expanduser()
    if p.suffix == ".md":
        return p
    repo, _, slug = target.rstrip("/").rpartition("/")
    if not repo or not slug:
        raise SystemExit(f"ark: «{target}» no es <repo>/<slug> ni la ruta de un plan.md")
    return paths.desk_dir() / repo / slug / "plan.md"


def cmd_status(args) -> int:
    fs = [f for f in desk.features() if not args.repo or f.repo == args.repo]
    if not fs:
        print(f"Sin features para {args.repo}." if args.repo else "Desk vacío: todavía no hay features.")
        return 0
    repo = None
    for f in fs:
        if f.repo != repo:
            repo = f.repo
            print(repo)
        print(f"  {f.slug:<28} spec {f.spec:<9} plan {f.plan:<7} run {f.run}")
    return 0


def cmd_plans(args) -> int:
    fs = [f for f in desk.features() if f.plan != "-"]
    if not fs:
        print("No hay planes en el desk.")
        return 0
    print(f"{'repo/slug':<40} {'plan':<7} {'aprobación':<11} run")
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
    rows = [f"{'#':<3}{'job':<18}{'PR':<44}{'tasks':<12}{'after':<14}{'tier':<5}presupuesto"]
    for i, j in enumerate(m["jobs"], 1):
        after = ",".join(map(str, j.get("after") or [])) or "-"
        b = j["budget"]
        rows.append(f"{i:<3}{j['id']:<18}{str(j['pr_title'])[:42]:<44}"
                    f"{','.join(map(str, j['tasks'])):<12}{after:<14}{j['tier']:<5}"
                    f"{b['minutes']} min · ${b['usd']}")
    rows.append(f"PR final: {m['final_pr_title']}")
    return "\n".join(rows)


def cmd_approve(args) -> int:
    if "ARK_ROLE" in os.environ:
        print("ark approve: rechazado. Lo está corriendo un proceso del runner (ARK_ROLE); "
              "solo Marco aprueba planes.", file=sys.stderr)
        return 2
    interactive = sys.stdin.isatty()
    if not interactive and os.environ.get("CLAUDECODE") != "1":
        print("ark approve: sin terminal solo se aprueba desde una sesión de Claude Code "
              "(su permiso es la confirmación); corrélo desde una terminal.", file=sys.stderr)
        return 2
    path = _resolve(args.target)
    try:
        sha_seen = sha256_file(path)
    except OSError as e:
        print(f"ark approve: no se pudo leer {path}: {e}", file=sys.stderr)
        return 1
    problems = validate.validate(path)
    if problems:
        print("No se puede aprobar: el plan no cumple el contrato.", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    plan = load_plan(path)
    print(_pr_table(plan.manifest))
    if interactive:
        answer = input("¿Aprobar este plan? [s/N] ").strip().lower()
        if answer not in ("s", "si", "sí", "y", "yes"):
            print("No aprobado.")
            return 1
    if sha256_file(path) != sha_seen:
        print("El plan cambió mientras se aprobaba; volvé a correr ark approve.", file=sys.stderr)
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
    print(f"Aprobado {repo}/{slug} · sha256 {sha[:12]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ark", description="Arkimedes desk (sin modelo).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="features del desk por repo")
    s.add_argument("repo", nargs="?")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("plans", help="todos los planes con su aprobación y su run")
    s.set_defaults(fn=cmd_plans)
    s = sub.add_parser("validate", help="el contrato del plan")
    s.add_argument("target", help="<repo>/<slug> o ruta a plan.md")
    s.add_argument("--approved", action="store_true", help="también chequea la aprobación")
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("approve", help="cerrar un plan (Marco confirma)")
    s.add_argument("target", help="<repo>/<slug>")
    s.set_defaults(fn=cmd_approve)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
