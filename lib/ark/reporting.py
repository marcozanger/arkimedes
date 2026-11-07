"""Making a run's report: steps.jsonl, report.html and, for a closed run, its history and the insights.
The loop calls generate() at close; `ark report` calls it for any run, old ones included."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import history, paths, report, run, stats, steps, summary
from .plan import load_plan


def plan_jobs(st: dict) -> dict:
    """The plan's jobs by id, for budgets and tasks. {} when the plan cannot be read any more."""
    try:
        return {j["id"]: j for j in load_plan(Path(st["plan"])).manifest["jobs"]}
    except Exception:
        return {}


def closed(st: dict) -> bool:
    return st.get("closing") == "done" and st.get("status") in ("done", "blocked")


def generate(st: dict, mjobs: dict, repo_dir, *, record: bool) -> dict:
    rows = steps.build(st)
    steps.write(st, rows)
    s = summary.summarize(st, rows, mjobs)
    path = report.write(st, s, rows)
    if record:
        history.record(st, s, mjobs, repo_dir)
        stats.write()
    return {"path": path, "summary": s, "markdown": report.summary_md(s, path)}


def open_file(path) -> None:
    subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(path)], check=False)


def _states() -> list[dict]:
    out = []
    for f in sorted(paths.runs_dir().glob("**/state.json")):
        try:
            st = json.loads(paths.read_regular(f, 8 << 20))
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and isinstance(st.get("repo"), str) and isinstance(st.get("slug"), str):
            out.append(st)
    return out


def _one(st: dict) -> dict:
    return generate(st, plan_jobs(st), paths.find_repo(st["repo"]), record=closed(st))


def cmd_report(args) -> int:
    if "ARK_ROLE" in os.environ:
        print("ark report: no corre dentro de un proceso del runner (ARK_ROLE): escribe el historial.", file=sys.stderr)
        return 2
    if args.all:
        done = [st for st in _states() if closed(st)]
        for st in done:
            print(_one(st)["path"])
        print(f"{len(done)} corridas cerradas con reporte; insights en {history.history_dir() / 'insights.md'}")
        return 0
    if not args.target:
        print("ark report: falta <repo>/<slug> (o --all)", file=sys.stderr)
        return 1
    repo, _, slug = args.target.rstrip("/").rpartition("/")
    st = run.load(repo, slug) if repo and slug else None
    if st is None:
        print(f"ark report: no hay corrida de {args.target}", file=sys.stderr)
        return 1
    out = _one(st)
    s = out["summary"]
    note = "" if closed(st) else " · sin historial: la corrida no cerró"
    print(f"{out['path']}\n{s['merged']} mergeados · {s['blocked']} bloqueados · {report.minutes(s['wall_minutes'])} · "
          f"{report.usd(s['usd'])}{note}")
    if args.open:
        open_file(out["path"])
    return 0


def cmd_stats(args) -> int:
    if "ARK_ROLE" in os.environ:
        print("ark stats: no corre dentro de un proceso del runner (ARK_ROLE).", file=sys.stderr)
        return 2
    path = stats.write()
    print(f"{path} · {len(path.read_text().splitlines())} líneas")
    return 0


def register(sub) -> None:
    s = sub.add_parser("report", help="el reporte de una corrida (y su historial si cerró)")
    s.add_argument("target", nargs="?", help="<repo>/<slug>")
    s.add_argument("--all", action="store_true", help="todas las corridas cerradas")
    s.add_argument("--open", action="store_true", help="abrir report.html")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("stats", help="reescribir history/insights.md")
    s.set_defaults(fn=cmd_stats)
