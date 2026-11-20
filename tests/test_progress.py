from ark import progress

PYTEST = """\
FAILED tests/test_a.py::test_one - AssertionError: assert 3 == 4
FAILED tests/test_b.py::test_two - TimeoutError
2 failed, 40 passed in 12.31s
"""


def test_red_ids_reads_pytest_jest_and_go():
    assert progress.red_ids(PYTEST) == ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    assert progress.red_ids(" FAIL src/a.test.ts\n  ✕ renders the list (12 ms)\n") == \
        ["renders the list", "src/a.test.ts"]
    assert progress.red_ids("--- FAIL: TestParse (0.00s)\n") == ["TestParse"]


def test_the_same_failure_with_other_timings_has_the_same_signature():
    other = PYTEST.replace("12.31s", "15.02s").replace("3 == 4", "3 == 4")
    assert progress.signature(PYTEST) == progress.signature(other)
    tmp = "Error: cannot open /private/var/folders/ab/xyz/T/a.db at 0x7ffee3b2a1c0"
    assert progress.signature(tmp) == progress.signature(tmp.replace("ab/xyz", "cd/uvw").replace("7ffee3", "7aaaa1"))


def test_a_different_red_test_is_a_different_signature():
    assert progress.signature(PYTEST) != progress.signature(PYTEST.replace("test_two", "test_three"))


def test_two_equal_failures_on_the_same_head_are_stalled():
    j = {}
    progress.note(j, PYTEST, "abc")
    assert not progress.stalled(j)
    progress.note(j, PYTEST, "abc")
    assert progress.stalled(j)
    assert "tests/test_a.py::test_one" in progress.why_stalled(j)


def test_a_new_head_or_a_new_failure_is_progress():
    j = {}
    progress.note(j, PYTEST, "abc")
    progress.note(j, PYTEST, "def")
    assert not progress.stalled(j)
    progress.note(j, PYTEST.replace("test_two", "test_nine"), "def")
    assert not progress.stalled(j)


def test_only_the_last_failures_are_kept():
    j = {}
    for i in range(progress.KEEP + 3):
        progress.note(j, f"error {i}", str(i))
    assert len(j["failures"]) == progress.KEEP
