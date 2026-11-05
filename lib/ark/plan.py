"""Read a desk plan: frontmatter, level-2 sections, task ids and the yaml Run manifest."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
TASK = re.compile(r"^### (T\d+)\b", re.M)
YAML_BLOCK = re.compile(r"^```ya?ml[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
HEADING = re.compile(r"^## (.+?)\s*$")


class PlanError(Exception):
    """The file cannot be read as a plan at all."""


@dataclass
class Plan:
    path: Path
    frontmatter: dict
    sections: dict[str, str]
    tasks: list[str]
    manifest: dict | None
    manifest_error: str | None


def split_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise PlanError(f"frontmatter is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PlanError("frontmatter is not a mapping")
    return data, text[m.end():]


def split_sections(body: str) -> dict[str, str]:
    """`## Heading` → its text, ignoring headings inside fenced code."""
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        m = None if fenced else HEADING.match(line)
        if m:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


def parse_manifest(section: str) -> tuple[dict | None, str | None]:
    m = YAML_BLOCK.search(section)
    if not m:
        return None, "no ```yaml block in `## Run manifest`"
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        return None, f"Run manifest is not valid YAML: {e}"
    if not isinstance(data, dict):
        return None, "Run manifest must be a YAML mapping"
    return data, None


def load_plan(path) -> Plan:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PlanError(f"cannot read {path}: {e}") from e
    fm, body = split_frontmatter(text)
    secs = split_sections(body)
    manifest, err = parse_manifest(secs.get("Run manifest", ""))
    return Plan(path, fm, secs, TASK.findall(secs.get("Tasks", "")), manifest, err)


def set_status(path, status: str) -> None:
    """Rewrite `status:` in the frontmatter only, atomically."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER.match(text)
    if not m:
        raise PlanError(f"{path} has no frontmatter")
    block = m.group(1)
    if re.search(r"^status:", block, re.M):
        block = re.sub(r"^status:.*$", f"status: {status}", block, count=1, flags=re.M)
    else:
        block = f"status: {status}\n{block}"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text[: m.start(1)] + block + text[m.end(1):], encoding="utf-8")
    tmp.replace(path)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
