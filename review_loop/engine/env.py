"""The environment an agent or a project command runs in: no tokens, no secrets, no credential helpers, no way to push.
The author CLI's own login is the one credential kept, and only an agent receives it."""

from __future__ import annotations

import fnmatch

ALWAYS_REMOVED = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "SSH_AUTH_SOCK", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                  "AWS_*", "*_SECRET*", "*PASSWORD*", "*_API_KEY")
AGENT_CREDENTIALS = ("CLAUDE_CODE_OAUTH_TOKEN",)
GIT_GUARDS = (("remote.origin.pushurl", "DISABLED"), ("push.default", "nothing"))


def sanitize_env(base_env: dict[str, str], patterns: list[str], shims_dir: str, *, agent: bool = False) -> dict[str, str]:
    """A prepare command or a check never receives the agent credential: what a process is not given, it cannot log."""
    removed = list(ALWAYS_REMOVED) + list(patterns)
    env = {key: value for key, value in base_env.items() if _kept(key, removed, agent)}
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


def _kept(key: str, removed: list[str], agent: bool) -> bool:
    if key in AGENT_CREDENTIALS:
        return agent
    return not any(fnmatch.fnmatchcase(key, pattern) for pattern in removed)
