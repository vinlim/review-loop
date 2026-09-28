"""The suite must pass as a review-loop verification check, which runs it in the agent environment."""

import os
import subprocess

import pytest

from review_loop.engine.env import sanitize_env
from review_loop.services.workspace import ensure_shims
from tests.gitenv import plain_git_env, sh


@pytest.fixture
def guarded(monkeypatch, tmp_path):
    env = sanitize_env(dict(os.environ), [], str(ensure_shims(tmp_path / "state")))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


def throwaway_repo(root):
    origin = root / "origin.git"
    sh(root, "git", "init", "--bare", "-q", "-b", "main", str(origin))
    local = root / "local"
    sh(root, "git", "init", "-q", "-b", "main", str(local))
    sh(local, "git", "config", "user.email", "t@example.com")
    sh(local, "git", "config", "user.name", "t")
    sh(local, "git", "commit", "-q", "--allow-empty", "-m", "one")
    sh(local, "git", "remote", "add", "origin", str(origin))
    return local


def test_the_tests_own_git_setup_pushes_to_its_throwaway_origin_under_the_coordinators_check_environment(guarded, tmp_path):
    local = throwaway_repo(tmp_path)

    inherited = subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, capture_output=True, text=True)
    assert inherited.returncode != 0 and "DISABLED" in inherited.stderr

    sh(local, "git", "push", "-q", "origin", "main")
    assert sh(local, "git", "ls-remote", "--heads", "origin", "main") != ""


def test_stripping_the_push_guard_keeps_the_rest_of_the_check_environment(guarded):
    env = plain_git_env()

    assert "GIT_CONFIG_COUNT" not in env and not any(key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) for key in env)
    for kept in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT", "GH_CONFIG_DIR", "PATH"):
        assert env[kept] == guarded[kept]
