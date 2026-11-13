"""The spec-review renderer copied into ui/vendor/spec-review/ for the control center page."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "ui" / "vendor" / "spec-review"
NAMES = ("md", "decisions", "fold", "doc", "strings")
HEADER = re.compile(r"^// Copied from ~/\.claude/skills/spec-review/scripts/lib/(\w+)\.mjs on \d{4}-\d{2}-\d{2}; keep in line by hand\.\n")


@pytest.mark.parametrize("name", NAMES)
def test_each_file_is_a_marked_copy_with_sibling_imports_only(name):
    text = (VENDOR / f"{name}.mjs").read_text()
    m = HEADER.match(text)
    assert m and m.group(1) == name
    for spec in re.findall(r"""^\s*import\s.*?from\s+["']([^"']+)["']""", text, re.M):
        assert re.fullmatch(r"\./[a-z]+\.mjs", spec), spec
    assert "node:" not in text and "hashSource" not in text


SAMPLE = """# Doc

## Design
<!-- fold -->

Intro text.

### Storage

- **SQLite** Alternatives: flat files. Pros: queries. Cons: a dependency. Why: it is already there.

### Other

More.
"""


def test_md_and_fold_render_a_section_with_a_card_and_a_fold():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = f"""
import {{ renderFoldable }} from {str((VENDOR / 'fold.mjs').as_uri())!r};
import {{ STRINGS }} from {str((VENDOR / 'strings.mjs').as_uri())!r};
import {{ parseDoc, bodyLines }} from {str((VENDOR / 'doc.mjs').as_uri())!r};
const doc = parseDoc(process.env.SRC, "spec.md");
const s = doc.sections[0];
console.log(s.title + "\\n" + renderFoldable(bodyLines(doc, s), s.level, STRINGS.en));
"""
    r = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30,
                       env={"SRC": SAMPLE, "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert out.startswith("Design\n")
    assert '<details class="fold">' in out and "Storage" in out
    assert "Alternatives" in out and 'class="dcard"' in out
