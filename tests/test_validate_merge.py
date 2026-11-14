import pytest

from ark.validate import _check_manifest
from helpers import manifest


def _problems(m):
    out = []
    _check_manifest(m, {"T1", "T2", "T3"}, lambda where, msg: out.append(msg))
    return out


GOOD = {"tier": "T2", "budget": {"minutes": 30, "usd": 4}, "against": ["arkimedes/x"], "overlap": ["lib/a.py"]}


def test_a_plan_without_merge_is_fine():
    assert _problems(manifest()) == []


def test_a_full_merge_entry_is_fine():
    assert _problems({**manifest(), "merge": GOOD}) == []


@pytest.mark.parametrize("bad, words", [
    ({**GOOD, "tier": "T9"}, "merge: tier"),
    ({k: v for k, v in GOOD.items() if k != "budget"}, "merge: missing `budget`"),
    ({**GOOD, "budget": {"minutes": 0, "usd": 4}}, "merge: budget"),
    ({**GOOD, "against": "arkimedes/x"}, "merge: `against`"),
    ({**GOOD, "colour": "red"}, "merge: unknown key `colour`"),
    ("T2", "`merge` must be a mapping"),
])
def test_bad_merge_entries_are_refused(bad, words):
    assert any(words in p for p in _problems({**manifest(), "merge": bad}))
