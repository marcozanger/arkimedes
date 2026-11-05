from ark import paths


def test_paths_follow_ark_home(ark_home):
    assert paths.ark_home() == ark_home
    assert paths.approval_file("muscleapp/mobile", "x") == ark_home / "approvals/muscleapp/mobile/x.json"
    assert paths.run_file("demo", "x") == ark_home / "runs/demo/x.md"


def test_find_repo_needs_origin(make_repo):
    with_origin = make_repo("demo")
    make_repo("local-only", origin=False)
    assert paths.find_repo("demo") == with_origin
    assert paths.find_repo("local-only") is None
    assert paths.find_repo("missing") is None


def test_find_repo_one_level_deeper(make_repo):
    nested = make_repo("muscleapp/mobile")
    assert paths.find_repo("muscleapp/mobile") == nested
    assert paths.find_repo("../demo") is None
    assert paths.find_repo("a/b/c") is None


def test_excluded_repo_is_not_found(make_repo, ark_home):
    make_repo("demo")
    (ark_home / "workspace.json").write_text(
        '{"roots": ["%s"], "exclude": ["demo"]}' % (ark_home.parent / "ws"))
    assert paths.find_repo("demo") is None
