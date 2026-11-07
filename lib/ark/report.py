"""runs/<repo>/<slug>/report.html: the run for Marco, in Spanish (spec §9a). A script, no model.

Everything in it comes from the run folder, where workers can write: every value is escaped. The decisions file
is the only markdown rendered, and only headings, bullets, **bold**, https links and the exact
`<details><summary>más</summary>` … `</details>` pair the format asks for."""
from __future__ import annotations

import re
from html import escape

from . import paths, run

DETAILS_OPEN = escape("<details><summary>más</summary>")
DETAILS_CLOSE = escape("</details>")
BOLD = re.compile(r"\*\*(.+?)\*\*")
LINK = re.compile(r"https://[^\s<>&\"']+")
KIND_ES = {"worker": "worker", "review": "revisión", "gate": "gate", "integrate": "integración",
           "judge": "juez", "intervene": "intervención", "decisions": "decisiones", "merge": "merge",
           "wait-marco": "espera a Marco"}
STATUS_ES = {"merged": "mergeado", "blocked": "bloqueado", "done": "terminado", "running": "corriendo",
             "held": "en pausa", "waiting": "esperando"}

CSS = """
:root { --bg:#fbfaf7; --fg:#1d1d1b; --muted:#6b6a65; --line:#e3e1da; --card:#ffffff; --ok:#1f7a4d; --bad:#b3261e; --est:#8a6d00; }
@media (prefers-color-scheme: dark) { :root { --bg:#151514; --fg:#ecebe6; --muted:#9c9a92; --line:#2e2d2a; --card:#1d1c1a; --ok:#5cc28f; --bad:#f2847b; --est:#e0c062; } }
body { background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, system-ui, sans-serif; margin:0; padding:24px 16px; }
main { max-width:1040px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:32px 0 8px; } h3 { font-size:15px; margin:16px 0 6px; }
.muted { color:var(--muted); } .ok { color:var(--ok); } .bad { color:var(--bad); } .est { color:var(--est); font-size:12px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:8px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
.tile b { display:block; font-size:18px; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
td.wrap { white-space:normal; min-width:240px; }
details { margin:4px 0 8px 16px; } summary { cursor:pointer; color:var(--muted); }
a { color:inherit; }
"""


def minutes(m) -> str:
    if m is None:
        return "–"
    m = round(m)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def usd(v) -> str:
    return "–" if v is None else f"${v:,.2f}"


def tokens(n) -> str:
    if not n:
        return "–"
    return f"{n / 1e6:.1f} M" if n >= 1e6 else f"{n / 1e3:.0f} k" if n >= 1e3 else str(n)


def _e(v) -> str:
    return escape(str(v if v is not None else "–"))


def _est(flag) -> str:
    return ' <span class="est">estimado</span>' if flag else ""


def _status(s) -> str:
    cls = "ok" if s in ("merged", "done") else "bad" if s == "blocked" else "muted"
    return f'<span class="{cls}">{_e(STATUS_ES.get(s, s))}</span>'


def _table(head: list[str], rows: list[list[str]], wrap: int | None = None) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f'<td{" class=wrap" if i == wrap else ""}>{c}</td>' for i, c in enumerate(r))
                   + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _inline(line: str) -> str:
    """One escaped line with **bold** and https links."""
    line = BOLD.sub(r"<strong>\1</strong>", line)
    return LINK.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', line)


def decisions_html(text: str | None) -> str:
    if not text:
        return '<p class="muted">Sin archivo de decisiones.</p>'
    out, depth, para = [], 0, []

    def flush():
        if para:
            out.append(f"<p>{' '.join(para)}</p>")
            para.clear()

    for raw in text.splitlines():
        line = escape(raw.strip())
        if line == DETAILS_OPEN:
            flush()
            out.append("<details><summary>más</summary>")
            depth += 1
        elif line == DETAILS_CLOSE:
            flush()
            if depth:                       # a close with nothing open is dropped
                out.append("</details>")
                depth -= 1
        elif line.startswith("# "):
            flush()                         # the file's own title: the page has one already
        elif line.startswith("## "):
            flush()
            out.append(f"<h3>{_inline(line[3:])}</h3>")
        elif line.startswith("- "):
            flush()
            out.append(f"<p>{_inline(line[2:])}</p>")
        elif line:
            para.append(_inline(line))
        else:
            flush()
    flush()
    out += ["</details>"] * depth            # one left open is closed
    return "\n".join(out)


def _summary(s: dict) -> str:
    est = _est(s["estimated"])
    tiles = [("Tiempo total", minutes(s["wall_minutes"])),
             ("Estimado (punta a punta)", minutes(s.get("estimated_minutes"))), ("Trabajando", minutes(s["working_minutes"])),
             ("Esperando a Marco", minutes(s["waiting_minutes"])), ("Tokens", tokens(s["tokens"]) + est),
             ("USD equivalente API", usd(s["usd"]) + est), ("Presupuesto", usd(s["budget_usd"])),
             ("PRs mergeados", str(s["merged"])), ("PRs bloqueados", str(s["blocked"])),
             ("Fallas de integración", str(s["integrate_fails"]))]
    final = f'<p>PR final: <a href="{_e(s["final_pr"])}">{_e(s["final_pr"])}</a></p>' if s["final_pr"] else \
        '<p class="muted">Sin PR final.</p>'
    return ('<div class="tiles">' + "".join(f'<div class="tile"><span class="muted">{k}</span><b>{v}</b></div>'
                                            for k, v in tiles) + "</div>" + final)


def _per_pr(s: dict) -> str:
    rows = []
    for jid, j in s["jobs"].items():
        pr = f'<a href="{_e(j["pr"])}">PR</a>' if j["pr"] else "–"
        rows.append([_e(jid), _status(j["status"]), _e(" → ".join(map(str, j["tiers"]))), str(j["attempts"]),
                     str(j["steers"]), str(j["reviews"]), str(j["gate_fails"]), str(j["integrate_fails"]),
                     f'{minutes(j["minutes"])} / {minutes(j["budget_minutes"])}', tokens(j["tokens"]),
                     f'{usd(j["usd"])} / {usd(j["budget_usd"])}{_est(j["estimated"])}', pr, _e(j["reason"] or "")])
    return _table(["job", "estado", "tiers", "intentos", "redirecciones", "revisiones", "gates rojos",
                   "integración roja", "minutos / presupuesto", "tokens", "USD / presupuesto", "PR", "motivo"],
                  rows, wrap=12)


def _timeline(rows: list[dict]) -> str:
    out = []
    for r in rows:
        when = (r["start"] or r["end"] or "")[11:16] or "–"
        out.append([str(r["step"]), _e(when), _e(KIND_ES.get(r["kind"], r["kind"])), _e(r["job"] or "–"),
                    _e(r["attempt"] or "–"), _e(r["model"] or "–"), minutes(r["minutes"]),
                    tokens(r["tokens_total"]), usd(r["usd"]) + _est(r["estimated"]), _e(r["outcome"]),
                    _e(r["reason"] or "")])
    return _table(["#", "hora (UTC)", "paso", "job", "intento", "modelo", "duración", "tokens", "USD",
                   "resultado", "motivo"], out, wrap=10)


def _agents(groups: dict, label: str, names: dict | None = None) -> str:
    rows = [[_e((names or {}).get(k, k)), str(g["n"]), minutes(g["minutes"]), tokens(g["tokens"]), usd(g["usd"])]
            for k, g in sorted(groups.items(), key=lambda kv: -kv[1]["usd"])]
    return _table([label, "pasos", "tiempo", "tokens", "USD"], rows)


def render_html(s: dict, rows: list[dict], decisions: str | None) -> str:
    title = f"Reporte · {s['repo']}/{s['slug']}"
    head = (f"<h1>{_e(title)}</h1><p class=muted>{_status(s['status'])} · desde {_e(s['started'])} "
            f"hasta {_e(s['ended'])} · los USD son el equivalente API que informa el CLI</p>")
    body = "".join([
        head,
        "<h2>Resumen</h2>", _summary(s),
        "<h2>Decisiones</h2>", decisions_html(decisions),
        "<h2>Por PR</h2>", _per_pr(s),
        "<h2>Línea de tiempo</h2>", _timeline(rows),
        "<h2>Agentes</h2>", "<h3>Por rol</h3>", _agents(s["by_kind"], "rol", KIND_ES),
        "<h3>Por modelo</h3>", _agents(s["by_model"], "modelo")])
    return (f'<!doctype html>\n<html lang="es"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_e(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>\n")


def write(st: dict, s: dict, rows: list[dict]):
    d = paths.run_dir(st["repo"], st["slug"])
    try:
        decisions = paths.read_regular(d / "decisions.md", 1 << 20).decode("utf-8", "replace")
    except OSError:
        decisions = None
    path = d / "report.html"
    run._atomic(path, render_html(s, rows, decisions))
    return path


def _vs(s: dict) -> str:
    e = s.get("estimated_minutes")
    return f" (estimated {minutes(e)})" if e is not None else ""


def summary_md(s: dict, path) -> str:
    """The report in a few lines for the final PR body, in English like the rest of it."""
    est = " (some numbers estimated)" if s["estimated"] else ""
    budget = f" of {usd(s['budget_usd'])} budget" if s["budget_usd"] is not None else ""
    lines = ["## Report", "",
             f"{minutes(s['wall_minutes'])} wall{_vs(s)} · {minutes(s['working_minutes'])} working · "
             f"{minutes(s['waiting_minutes'])} waiting for Marco · {tokens(s['tokens'])} tokens · "
             f"{usd(s['usd'])} API-equivalent{budget}{est}",
             f"{s['merged']} merged · {s['blocked']} blocked · {s['integrate_fails']} integration failures", "",
             "| job | status | tiers | attempts | reviews | minutes | USD |", "|---|---|---|---|---|---|---|"]
    for jid, j in s["jobs"].items():
        lines.append(f"| {jid} | {j['status']} | {' → '.join(map(str, j['tiers']))} | {j['attempts']} | "
                     f"{j['reviews']} | {minutes(j['minutes'])} | {usd(j['usd'])} |")
    lines += ["", f"Full report (local): `{path}`"]
    return "\n".join(lines).replace("@", "@​") + "\n"    # no mention reaches anyone
