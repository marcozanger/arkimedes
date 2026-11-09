"""Guard: every text in the repo is English (the review pages and desk/ are the exception)."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SPANISH = re.compile(
    r"[áéíóúñÁÉÍÓÚÑ¿¡]"
    r"| (?:que|para|los|las|una|todavía|ningún|ninguna|está) ",
    re.IGNORECASE,
)

SKIP_PREFIXES = ("desk/",)
SKIP_SUFFIXES = (".review.html", ".review.json")

# (path, substring): a hit line containing the substring is allowed. Each has a reason.
ALLOWLIST = [
    # legacy decisions marker still accepted when reading old decisions files
    ("lib/ark/report.py", "más"),
    # test fixture for that legacy marker
    ("tests/test_report.py", "más"),
    # Spanish s/si/sí answers still accepted by the approval prompt
    ("lib/ark/cli.py", "sí"),
    # test of those answers
    ("tests/test_cli.py", "sí"),
    # non-ASCII path example
    ("tests/test_proc.py", "diseño"),
    # the Spanish heading the ark-plan skill adds to the review copy
    ("skills/ark-plan/SKILL.md", "## Presupuesto"),
    # test of that heading
    ("tests/test_ark_plan_skill.py", "## Presupuesto"),
    # trigger phrases in Spanish that Marco types to start the skill
    ("skills/ark-plan/SKILL.md", "planificá"),
    # the old spec quotes the cli.py answers above
    ("specs/2026-11-05-p1-desk-contract-plan.md", '"sí"'),
    # `para` is an English-code variable name (paragraph)
    ("lib/ark/report.py", "para"),
    # non-ASCII test data: a path, a character and an @-mention edge case
    ("lib/ark/proc.py", "café"),
    ("tests/test_brief.py", "é@octocat"),
    ("tests/test_gitops.py", "año"),
    ("tests/test_loop.py", "é"),
    # the legacy `más` marker must stay absent from new briefs
    ("tests/test_brief.py", "más"),
    # the report test checks the Spanish headings are gone and `más` still reads
    ("tests/test_report_render.py", "Resumen"),
    ("tests/test_report_render.py", "más"),
    # this file lists the Spanish markers it looks for
    ("tests/test_english_only.py", ""),
]


def _tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def _allowed(path, line):
    return any(path == p and s in line for p, s in ALLOWLIST)


def test_texts_are_english():
    hits = []
    for path in _tracked_files():
        if path.startswith(SKIP_PREFIXES) or path.endswith(SKIP_SUFFIXES):
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary, or a path that is not a regular file
        for n, line in enumerate(text.splitlines(), 1):
            if SPANISH.search(line) and not _allowed(path, line):
                hits.append(f"{path}:{n}: {line.strip()}")
    assert not hits, "Spanish text found:\n" + "\n".join(hits)
