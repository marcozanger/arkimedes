import pytest

from ark.companions import is_companion


@pytest.mark.parametrize("path,yes", [
    ("tests/test_brief.py", True), ("tests/conftest.py", True), ("tests/fixtures/a.json", True),
    ("test/a.py", True), ("tests", False),
    ("packages/ui/src/__tests__/a.ts", True), ("src/__tests__", False),
    ("src/notes.test.ts", True), ("src/notes.spec.tsx", True), ("src/notes.test", False),
    ("conftest.py", True), ("pkg/conftest.py", True),
    ("docs/decisions/2026-11-01-x.md", True), ("docs/runner-control.md", True), ("docs/a/b.md", False),
    ("docs/image.png", False), ("docs/.md", False),
    ("lib/ark/loop.py", False), ("src/notes.ts", False), ("tsconfig.json", False), ("vitest.config.ts", False),
    ("package.json", False), ("README.md", False), ("pkg/tests/a.py", False), ("mytests/a.py", False),
    ("tests/../lib/a.py", False), ("/tests/a.py", False), ("", False), (".git/tests/a", False),
    ("tests\\a.py", False),
])
def test_is_companion(path, yes):
    assert is_companion(path) is yes


def test_a_non_string_is_no_companion():
    assert is_companion(None) is False and is_companion(5) is False
