"""The environment an agent or a project command runs in: no tokens, no secrets, no credential helpers, no way to push.
The author CLI's login travels with its adapter, never through this environment."""

from __future__ import annotations

import fnmatch

ALWAYS_REMOVED = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "SSH_AUTH_SOCK", "OPENAI_API_KEY",
                  "ANTHROPIC_API_KEY", "AWS_*", "*_SECRET*", "*PASSWORD*", "*_API_KEY")
# An empty credential.helper resets the helper list, so a helper in the shared .git/config cannot answer either.
GIT_GUARDS = (("remote.origin.pushurl", "DISABLED"), ("push.default", "nothing"), ("credential.helper", ""))


def sanitize_env(base_env: dict[str, str], patterns: list[str], shims_dir: str) -> dict[str, str]:
    removed = list(ALWAYS_REMOVED) + list(patterns)
    env = {key: value for key, value in base_env.items() if not any(fnmatch.fnmatchcase(key, pattern) for pattern in removed)}
    env["PATH"] = f"{shims_dir}:{base_env.get('PATH', '')}"
    env["GIT_CONFIG_GLOBAL"] = f"{shims_dir}/empty-gitconfig"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_CONFIG_DIR"] = f"{shims_dir}/empty-gh-config"
    env["GIT_CONFIG_COUNT"] = str(len(GIT_GUARDS))
    for index, (key, value) in enumerate(GIT_GUARDS):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env
