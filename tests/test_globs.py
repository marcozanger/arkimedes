import pytest

from ark.globs import areas_overlap, overlap


@pytest.mark.parametrize("a,b,expected", [
    ("src/shell/**", "src/ui/**", False),
    ("src/**", "src/ui/x.ts", True),
    ("src/a.ts", "src/b.ts", False),
    ("src/a.ts", "src/a.ts", True),
    ("**", "anything/at/all", True),
    ("src/*.ts", "src/*.css", True),  # conservative: same folder, wildcard names
    ("./src/ui/", "src/ui/Button.tsx", True),
])
def test_overlap(a, b, expected):
    assert overlap(a, b) is expected
    assert overlap(b, a) is expected


def test_areas_overlap_any_pair():
    assert areas_overlap(["a/**", "b/x"], ["c/**", "b/x"])
    assert not areas_overlap(["a/**"], ["b/**"])


from ark.globs import match, match_any


def test_match_double_star():
    assert match("src/shell/a.py", "src/shell/**")
    assert match("src/shell/deep/b.py", "src/shell/**")
    assert not match("src/ui/a.py", "src/shell/**")
    assert match("a/b/c.test.ts", "**/*.test.ts")
    assert match("c.test.ts", "**/*.test.ts")


def test_match_single_star_stays_in_one_segment():
    assert match("lib/x.dart", "lib/*.dart")
    assert not match("lib/sub/x.dart", "lib/*.dart")


def test_literal_folder_matches_files_inside():
    assert match("docs/a.md", "docs")
    assert match("docs/a.md", "docs/")
    assert not match("docsx/a.md", "docs")
    assert match("README.md", "./README.md")


def test_match_any():
    assert match_any("src/ui/a.tsx", ["src/shell/**", "src/ui/**"])
    assert not match_any("x", [])
