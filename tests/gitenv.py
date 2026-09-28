"""The git config guard the coordinator puts in a check's environment, and how the suite takes it off."""

from __future__ import annotations

GUARD_KEYS = ("GIT_CONFIG_COUNT",)
GUARD_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def without_git_guard(env: dict[str, str]) -> dict[str, str]:
    """`env` without the GIT_CONFIG_* entries; the credential cut-offs and the shims stay."""
    return {key: value for key, value in env.items() if key not in GUARD_KEYS and not key.startswith(GUARD_PREFIXES)}
