import subprocess

from review_loop.engine.env import sanitize_env


BASE = {"PATH": "/usr/bin", "HOME": "/h", "GH_TOKEN": "x", "GITHUB_TOKEN": "x", "SSH_AUTH_SOCK": "/tmp/s", "OPENAI_API_KEY": "k",
        "ANTHROPIC_API_KEY": "k", "AWS_SECRET_ACCESS_KEY": "k", "DB_PASSWORD": "p", "CLAUDE_CODE_OAUTH_TOKEN": "keep", "LANG": "C"}


def test_tokens_agent_sockets_api_keys_and_the_author_cli_login_never_reach_a_process_but_home_does():
    env = sanitize_env(BASE, [], "/shims")

    for gone in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD",
                 "CLAUDE_CODE_OAUTH_TOKEN"):
        assert gone not in env
    assert env["HOME"] == "/h" and env["LANG"] == "C"


def test_the_agent_environment_disables_pushes_through_git_config_variables_not_the_shared_config():
    env = sanitize_env({"PATH": "/usr/bin"}, [], "/shims")

    keys = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert keys["remote.origin.pushurl"] == "DISABLED"
    assert keys["push.default"] == "nothing"
    assert keys["credential.helper"] == ""


def test_git_and_gh_credential_lookups_are_cut_off_for_agents():
    env = sanitize_env({"PATH": "/usr/bin", "HOME": "/h"}, [], "/shims")

    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_GLOBAL"] == "/shims/empty-gitconfig" and env["GH_CONFIG_DIR"] == "/shims/empty-gh-config"


def test_a_credential_helper_in_the_repository_config_answers_nobody_in_the_sanitized_environment(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "credential.helper", "!f() { echo username=alice; echo password=hunter2; }; f"], check=True)
    ask = "protocol=https\nhost=example.com\n\n"
    plain = subprocess.run(["git", "credential", "fill"], cwd=repo, input=ask, capture_output=True, text=True)
    assert "password=hunter2" in plain.stdout, "the local helper answers in an ordinary shell"

    env = sanitize_env({"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, [], str(tmp_path / "shims"))
    guarded = subprocess.run(["git", "credential", "fill"], cwd=repo, input=ask, capture_output=True, text=True, env=env)

    assert "hunter2" not in guarded.stdout and "hunter2" not in guarded.stderr


def test_a_configured_value_restores_a_stripped_variable_with_the_pull_request_number_filled_in():
    env = sanitize_env({"PATH": "/usr/bin", "DB_DATABASE": "app"}, ["DB_*"], "/shims",
                       {"DB_DATABASE": "app_test_{pr}", "LABEL": "{x}-{pr}"}, "22")

    assert env["DB_DATABASE"] == "app_test_22"
    assert env["LABEL"] == "{x}-22", "only {pr} is a placeholder; other braces stay as written"


def test_a_configured_value_cannot_loosen_the_path_or_git_guards():
    env = sanitize_env({"PATH": "/usr/bin"}, [], "/shims",
                       {"PATH": "/evil", "GIT_CONFIG_GLOBAL": "/evil/gitconfig", "GIT_CONFIG_COUNT": "0"}, "22")

    assert env["PATH"] == "/shims:/usr/bin"
    assert env["GIT_CONFIG_GLOBAL"] == "/shims/empty-gitconfig" and env["GIT_CONFIG_COUNT"] == "3"


def test_a_configured_value_cannot_set_a_name_the_tool_always_removes():
    env = sanitize_env({"PATH": "/usr/bin", "DB_DATABASE": "app"}, ["DB_*"], "/shims",
                       {"GH_TOKEN": "t", "CLAUDE_CODE_OAUTH_TOKEN": "t", "DB_PASSWORD": "p", "DB_DATABASE": "app_test_{pr}"}, "22")

    assert "GH_TOKEN" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "DB_PASSWORD" not in env, "a wildcard in the always-removed list holds as well"
    assert env["DB_DATABASE"] == "app_test_22", "a name only the repository's patterns strip can still be set"


def test_git_command_line_config_cannot_restore_a_credential_helper_or_an_origin_push_url(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(tmp_path / "origin.git")], check=True)
    parameters = ("'credential.helper'='!f() { echo username=alice; echo password=hunter2; }; f' "
                  "'remote.origin.pushurl'='' 'remote.origin.pushurl'='/virtual/destination'")
    ask = "protocol=https\nhost=example.com\n\n"
    base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    shims = str(tmp_path / "shims")

    for env in (sanitize_env(base, [], shims, {"GIT_CONFIG_PARAMETERS": parameters}, "22"),
                sanitize_env({**base, "GIT_CONFIG_PARAMETERS": parameters}, [], shims)):
        filled = subprocess.run(["git", "credential", "fill"], cwd=repo, input=ask, capture_output=True, text=True, env=env)
        pushes = subprocess.run(["git", "remote", "get-url", "--push", "--all", "origin"], cwd=repo, capture_output=True, text=True, env=env)

        assert "hunter2" not in filled.stdout and "hunter2" not in filled.stderr
        assert pushes.stdout.split() == ["DISABLED"]
