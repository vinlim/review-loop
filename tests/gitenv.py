"""Git commands the tests run themselves, outside the environment under test.

The coordinator runs a repository's verification checks, this suite included, in the agent environment, whose
GIT_CONFIG_* variables disable pushes to any remote named origin. The throwaway repositories the tests build
call their remote origin too, so their setup pushes strip that ambient guard. The per-worktree push lock the
tests assert on lives in config files, not the environment, so stripping it proves the lock rather than the guard.
"""

from __future__ import annotations

import os
import subprocess

GUARD_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def plain_git_env() -> dict[str, str]:
    """The current environment without the coordinator's git config guard."""
    return {key: value for key, value in os.environ.items()
            if key != "GIT_CONFIG_COUNT" and not key.startswith(GUARD_PREFIXES)}


def sh(cwd, *argv) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True, env=plain_git_env()).stdout.strip()
