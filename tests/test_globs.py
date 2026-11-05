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
