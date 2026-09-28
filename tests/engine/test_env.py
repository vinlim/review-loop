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

