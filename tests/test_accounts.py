import pytest
from helpers import set_accounts

from ark import accounts


@pytest.fixture
def gh_tokens(tmp_path, monkeypatch):
    """A gh that knows one user, marcozanger-gl. Returns the file with every call and the tokens it saw."""
    d = tmp_path / "ghbin"
    d.mkdir()
    exe = d / "gh"
    exe.write_text(f"""#!/bin/sh
echo "$* GH_TOKEN=$GH_TOKEN GITHUB_TOKEN=$GITHUB_TOKEN" >> "{d}/argv"
[ "$1 $2 $3" = "auth token --user" ] || exit 2
case "$4" in
  marcozanger-gl) echo tok-gl ;;
  *) echo "no oauth token found for $4" >&2; exit 1 ;;
esac
""")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    return d / "argv"


def test_no_rule_is_the_personal_account(ark_home):
    assert accounts.rule_for("demo") == {}
    assert accounts.claude_env("demo") == {}
    assert accounts.gh_env("demo") == {}


def test_match_is_a_folder_prefix_and_the_first_rule_wins(ark_home):
    set_accounts(ark_home, [{"match": "globallogic/proposals/", "gh_user": "a"},
                            {"match": "globallogic", "gh_user": "b"}])
    assert accounts.rule_for("globallogic/proposals/v0")["gh_user"] == "a"
    assert accounts.rule_for("globallogic/other")["gh_user"] == "b"
    assert accounts.rule_for("globallogic")["gh_user"] == "b"
    assert accounts.rule_for("globallogicx/app") == {}


def test_claude_env_points_at_the_rule_dir(ark_home, tmp_path):
    work = tmp_path / "claude-work"
    work.mkdir()
    set_accounts(ark_home, [{"match": "gl/", "claude_config_dir": str(work)}])
    assert accounts.claude_env("gl/v0") == {"CLAUDE_CONFIG_DIR": str(work)}


def test_claude_env_expands_home(ark_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude-work").mkdir()
    set_accounts(ark_home, [{"match": "gl", "claude_config_dir": "~/.claude-work"}])
    assert accounts.claude_env("gl/v0") == {"CLAUDE_CONFIG_DIR": str(tmp_path / ".claude-work")}


def test_missing_config_dir_never_falls_back(ark_home, tmp_path):
    set_accounts(ark_home, [{"match": "gl/", "claude_config_dir": str(tmp_path / "nope")}])
    with pytest.raises(accounts.AccountError, match="does not exist"):
        accounts.claude_env("gl/v0")


def test_gh_env_asks_gh_for_the_user_token(ark_home, gh_tokens, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "personal")
    monkeypatch.setenv("GITHUB_TOKEN", "personal")
    set_accounts(ark_home, [{"match": "gl/", "gh_user": "marcozanger-gl"}])
    assert accounts.gh_env("gl/v0") == {"GH_TOKEN": "tok-gl"}
    # gh is asked without the inherited tokens: with one set, `gh auth token` returns that one
    assert gh_tokens.read_text() == "auth token --user marcozanger-gl GH_TOKEN= GITHUB_TOKEN=\n"


def test_gh_without_the_user_never_falls_back(ark_home, gh_tokens):
    set_accounts(ark_home, [{"match": "gl/", "gh_user": "ghost"}])
    with pytest.raises(accounts.AccountError, match="ghost"):
        accounts.gh_env("gl/v0")


def test_a_rule_without_gh_user_asks_gh_nothing(ark_home, gh_tokens, tmp_path):
    (tmp_path / "cw").mkdir()
    set_accounts(ark_home, [{"match": "gl/", "claude_config_dir": str(tmp_path / "cw")}])
    assert accounts.gh_env("gl/v0") == {}
    assert not gh_tokens.exists()


def test_check_tries_both(ark_home, gh_tokens, tmp_path):
    (tmp_path / "cw").mkdir()
    set_accounts(ark_home, [{"match": "gl/", "claude_config_dir": str(tmp_path / "cw"), "gh_user": "ghost"}])
    with pytest.raises(accounts.AccountError, match="ghost"):
        accounts.check("gl/v0")
    accounts.check("demo")   # no rule: nothing to check


@pytest.mark.parametrize("rules", [
    {"match": "gl/"},                          # not a list
    ["gl/"],                                   # a rule that is not a mapping
    [{"gh_user": "x"}],                        # no match
    [{"match": "gl/", "user": "x"}],           # unknown key
    [{"match": "/", "gh_user": "x"}],          # empty match
    [{"match": "gl/", "gh_user": ""}],         # empty value
    [{"match": "gl/", "gh_user": 3}],          # not a string
])
def test_malformed_rules_are_errors(ark_home, rules):
    set_accounts(ark_home, rules)
    with pytest.raises(accounts.AccountError, match="accounts"):
        accounts.rule_for("demo")
