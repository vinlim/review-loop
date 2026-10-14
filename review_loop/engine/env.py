"""The environment an agent or a project command runs in: no tokens, no secrets, no credential helpers, no way to push.
The author CLI's login travels with its adapter, never through this environment."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping

ALWAYS_REMOVED = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "SSH_AUTH_SOCK", "OPENAI_API_KEY",
                  "ANTHROPIC_API_KEY", "AWS_*", "*_SECRET*", "*PASSWORD*", "*_API_KEY")
# An empty credential.helper resets the helper list, so a helper in the shared .git/config cannot answer either.
GIT_GUARDS = (("remote.origin.pushurl", "DISABLED"), ("push.default", "nothing"), ("credential.helper", ""))


def sanitize_env(base_env: dict[str, str], patterns: list[str], shims_dir: str, overrides: Mapping[str, str] | None = None,
                 pr: str = "") -> dict[str, str]:
    """`overrides` are the repository's configured values, with "{pr}" filled in. They land after the patterns strip and before
    the guards below, so a configured value can restore a variable the repository's patterns stripped, but never set a name
    in ALWAYS_REMOVED or loosen a guard."""
    removed = list(ALWAYS_REMOVED) + list(patterns)
    env = {key: value for key, value in base_env.items() if not any(fnmatch.fnmatchcase(key, pattern) for pattern in removed)}
    env.update({key: value.replace("{pr}", pr) for key, value in (overrides or {}).items()
                if not any(fnmatch.fnmatchcase(key, pattern) for pattern in ALWAYS_REMOVED)})
    # Git applies the `git -c` settings this carries after the GIT_CONFIG_COUNT guards, so it goes whether inherited or configured.
    env.pop("GIT_CONFIG_PARAMETERS", None)
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
