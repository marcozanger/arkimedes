"""The coach queue of a repo: ~/.claude/coach/queue/<basename of its main checkout>.md, one task per line,
`- [ ] YYYY-MM-DD · <text> (from: <where>)`. The runner adds one item per run with flaky gates."""
from __future__ import annotations

from datetime import date
from pathlib import Path

ITEM_CHARS = 900


def queue_file(repo_dir) -> Path:
    return Path.home() / ".claude" / "coach" / "queue" / f"{Path(repo_dir).name}.md"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def flaky_text(st: dict) -> str:
    gates = "; ".join(f"{f['job']} ({'integration gate' if f['kind'] == 'integrate' else 'job gate'}): "
                      f"{_one_line(f['note'])}" for f in st["flaky"])
    text = (f"Make these tests robust: they failed under load and passed alone in the ark run "
            f"{st['repo']}/{st['slug']}: {gates}")
    return text[:ITEM_CHARS]


def add_flaky(repo_dir, st: dict) -> Path:
    """Append the run's item. Raises OSError."""
    f = queue_file(repo_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    line = f"- [ ] {date.today().isoformat()} · {flaky_text(st)} (from: ark:{st['repo']}/{st['slug']})\n"
    with open(f, "a+", encoding="utf-8") as fh:
        fh.seek(0, 2)
        if fh.tell():
            fh.seek(fh.tell() - 1)
            if fh.read(1) != "\n":
                line = "\n" + line
        fh.write(line)
    return f
