from review_loop.engine.env import sanitize_env


def test_tokens_agent_sockets_and_api_keys_never_reach_an_agent_but_the_oauth_token_and_home_do():
    base = {"PATH": "/usr/bin", "HOME": "/h", "GH_TOKEN": "x", "GITHUB_TOKEN": "x", "SSH_AUTH_SOCK": "/tmp/s", "OPENAI_API_KEY": "k",
            "ANTHROPIC_API_KEY": "k", "AWS_SECRET_ACCESS_KEY": "k", "DB_PASSWORD": "p", "CLAUDE_CODE_OAUTH_TOKEN": "keep", "LANG": "C"}

    env = sanitize_env(base, [], "/shims")

    for gone in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD"):
        assert gone not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep" and env["HOME"] == "/h" and env["LANG"] == "C"


def test_the_agent_environment_disables_pushes_through_git_config_variables_not_the_shared_config():
    env = sanitize_env({"PATH": "/usr/bin"}, [], "/shims")

    keys = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert keys["remote.origin.pushurl"] == "DISABLED"
    assert keys["push.default"] == "nothing"


def test_git_and_gh_credential_lookups_are_cut_off_for_agents():
    env = sanitize_env({"PATH": "/usr/bin", "HOME": "/h"}, [], "/shims")

    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_GLOBAL"] == "/shims/empty-gitconfig" and env["GH_CONFIG_DIR"] == "/shims/empty-gh-config"
