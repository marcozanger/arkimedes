"""Marco's verdicts on a closed run's decisions file (runs/<repo>/<slug>/decisions.md), with no model.

Each `- **D<n>** · …` line is one decision. A verdict goes to history/euklides/reviews.jsonl, the file
`ark review` already writes, under the id `<repo>/<slug>/D<n>` (verdict `ok` or `wrong`, plus the decision's
text, which is what the distill reads). Whether the run needs a fix or more work is one more row under
`<repo>/<slug>/followup` (verdict `none`, `fix` or `more`). The last row on an id wins."""
from __future__ import annotations

import re

from . import euklides, paths

ITEM = re.compile(r"^- \*\*(D\d+)\*\*\s*(.*)$")
VERDICTS = {"ok": "ok", "change": "wrong"}   # what the page says -> what reviews.jsonl keeps
FOLLOWUPS = ("none", "fix", "more")
MAX_COMMENT = 4000


class RunReviewError(Exception):
    def __init__(self, message: str, kind: str = "invalid"):
        super().__init__(message)
        self.kind = kind


def parse(text: str) -> list[dict]:
    """The decisions of a decisions.md: id, the job heading it sits under and its text (the bullet and its
    indented lines, without the `<details>` tags)."""
    items, job = [], ""
    for line in text.splitlines():
        h = re.match(r"^## (.+)$", line)
        m = ITEM.match(line)
        if h:
            job = h.group(1).strip()
        elif m:
            items.append({"id": m.group(1), "job": job, "lines": [m.group(2).strip()]})
        elif items and line.startswith(" "):
            items[-1]["lines"].append(line.strip())
    out = []
    for i in items:
        body = [x for x in i["lines"] if x and not re.match(r"^</?details>|^<summary>", x)]
        out.append({"id": i["id"], "job": i["job"], "text": "\n".join(body)})
    return out


def _file(repo: str, slug: str) -> str:
    try:
        return (paths.run_dir(repo, slug) / "decisions.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def load(repo: str, slug: str) -> dict:
    """{items: [{id, job, text, verdict, comment}], followup: {kind, comment} | None}. A verdict counts only
    while the decision's text is the one it was given on."""
    rows = {r.get("id"): r for r in euklides.reviews()}
    out = []
    for i in parse(_file(repo, slug)):
        r = rows.get(f"{repo}/{slug}/{i['id']}")
        fresh = r is not None and r.get("text") == i["text"]
        out.append({**i, "verdict": ("ok" if r.get("verdict") == "ok" else "change") if fresh else None,
                    "comment": (r.get("comment") or "") if fresh else ""})
    f = rows.get(f"{repo}/{slug}/followup")
    return {"items": out, "followup": {"kind": f["verdict"], "comment": f.get("comment", "")} if f else None}


def pending(repo: str, slug: str) -> list[dict]:
    return [i for i in load(repo, slug)["items"] if i["verdict"] is None]


def save(repo: str, slug: str, key: str, verdict: str, comment: str = "") -> dict:
    if not isinstance(comment, str) or len(comment) > MAX_COMMENT:
        raise RunReviewError("the comment must be text of at most 4000 characters")
    if key == "followup":
        if verdict not in FOLLOWUPS:
            raise RunReviewError(f"verdict must be one of {', '.join(FOLLOWUPS)}")
        euklides.add_review(f"{repo}/{slug}/followup", verdict, comment.strip())
        return load(repo, slug)
    if verdict not in VERDICTS:
        raise RunReviewError("verdict must be ok or change")
    item = next((i for i in parse(_file(repo, slug)) if i["id"] == key), None)
    if item is None:
        raise RunReviewError(f"no decision {key} in this run", "unknown")
    if verdict == "change" and not comment.strip():
        raise RunReviewError("say what is wrong: a Change needs a comment")
    euklides.add_review(f"{repo}/{slug}/{key}", VERDICTS[verdict], comment.strip(), text=item["text"])
    return load(repo, slug)
