"""review.json: Marco's per-section verdicts on a feature's spec.md and plan.md.

A section is a `## ` block outside code fences. A verdict counts only while the section's text hash is
the one it was given on, so editing a section resets that section and no other."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .plan import PlanError, split_frontmatter

DOCS = {"spec": "spec.md", "plan": "plan.md"}
VERDICTS = ("ok", "change")
MAX_NOTE = 4000
WARNING = "review.json could not be read; it is rewritten on the next save"


class ReviewError(Exception):
    pass


def _body(text: str) -> str:
    try:
        return split_frontmatter(text)[1]
    except PlanError:
        return text


def sections(text: str) -> list[dict]:
    out: list[tuple[str, list[str]]] = []
    fence = None
    for line in _body(text).splitlines():
        m = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if m:
            f = m.group(1)
            if fence is None:
                fence = f
            elif f[0] == fence[0] and len(f) >= len(fence) and line.strip() == f:
                fence = None
        elif fence is None:
            h = re.match(r"^## (.+)$", line)
            if h:
                out.append((h.group(1).strip(), [line]))
                continue
        if out:
            out[-1][1].append(line)
    res, seen = [], {}
    for title, lines in out:
        base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f"{base}-{seen[base]}"
        res.append({"key": key, "title": title, "hash": hashlib.sha256("\n".join(lines).encode()).hexdigest()})
    return res


def _read_doc(folder: Path, doc: str) -> str:
    if doc not in DOCS:
        raise ReviewError(f"unknown document {doc!r}")
    try:
        return (Path(folder) / DOCS[doc]).read_text(encoding="utf-8")
    except OSError:
        raise ReviewError(f"{DOCS[doc]} does not exist") from None


def _stored(folder: Path) -> tuple[dict, bool]:
    f = Path(folder) / "review.json"
    if not f.exists():
        return {"version": 1, "docs": {}}, False
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("docs"), dict):
            raise ValueError("shape")
    except (OSError, ValueError):
        return {"version": 1, "docs": {}}, True
    return d, False


def load(folder, doc: str) -> dict:
    folder = Path(folder)
    try:
        text = _read_doc(folder, doc)
    except ReviewError:
        text = ""
    stored, bad = _stored(folder)
    entries = stored["docs"].get(doc)
    entries = entries.get("sections") if isinstance(entries, dict) else None
    entries = entries if isinstance(entries, dict) else {}
    out = []
    for s in sections(text):
        e = entries.get(s["key"])
        ok = isinstance(e, dict) and e.get("hash") == s["hash"] and e.get("verdict") in VERDICTS
        out.append({**s, "verdict": e["verdict"] if ok else None, "note": str(e.get("note") or "") if ok else "",
                    "at": e.get("at") if ok else None})
    return {"doc": doc, "sha256": hashlib.sha256(text.encode()).hexdigest(), "sections": out,
            "warning": WARNING if bad else None}


def save(folder, doc: str, key: str, verdict, note: str) -> None:
    folder = Path(folder)
    text = _read_doc(folder, doc)
    if verdict is not None and verdict not in VERDICTS:
        raise ReviewError(f"verdict must be ok, change or none, not {verdict!r}")
    note = note or ""
    if not isinstance(note, str) or len(note) > MAX_NOTE:
        raise ReviewError(f"the note is longer than {MAX_NOTE} characters")
    by_key = {s["key"]: s for s in sections(text)}
    if key not in by_key:
        raise ReviewError(f"no section {key!r} in {DOCS[doc]}")
    stored, _ = _stored(folder)
    entry = stored["docs"].get(doc)
    entry = entry if isinstance(entry, dict) else {}
    secs = entry.get("sections") if isinstance(entry.get("sections"), dict) else {}
    # drop entries of sections that no longer exist or changed, then set this one
    secs = {k: v for k, v in secs.items() if k in by_key and isinstance(v, dict) and v.get("hash") == by_key[k]["hash"]}
    if verdict is None:
        secs.pop(key, None)
    else:
        secs[key] = {"verdict": verdict, "note": note, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "hash": by_key[key]["hash"]}
    stored["docs"][doc] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "sections": secs}
    stored["version"] = 1
    f = folder / "review.json"
    tmp = f.with_name("review.json.tmp")
    tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)


def summary(folder, doc: str) -> dict:
    secs = load(folder, doc)["sections"]
    return {"ok": sum(s["verdict"] == "ok" for s in secs), "change": sum(s["verdict"] == "change" for s in secs),
            "total": len(secs)}
