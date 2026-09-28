"""The suite must pass as a review-loop verification check, which runs it in the agent environment."""

import os
import subprocess
import sys
from pathlib import Path

from review_loop.engine.env import sanitize_env
from review_loop.services.workspace import ensure_shims
from tests.gitenv import without_git_guard

ROOT = Path(__file__).resolve().parents[1]


def sh(cwd, *argv, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, **kwargs)


def throwaway_repo(root):
    origin = root / "origin.git"
    sh(root, "git", "init", "--bare", "-q", "-b", "main", str(origin), check=True)
    local = root / "local"
    sh(root, "git", "init", "-q", "-b", "main", str(local), check=True)
    sh(local, "git", "config", "user.email", "t@example.com", check=True)
    sh(local, "git", "config", "user.name", "t", check=True)
    sh(local, "git", "config", "credential.helper", "!f() { echo username=alice; echo password=hunter2; }; f", check=True)
    sh(local, "git", "commit", "-q", "--allow-empty", "-m", "one", check=True)
    sh(local, "git", "remote", "add", "origin", str(origin), check=True)
    return local


def test_a_test_owned_repository_pushes_to_its_origin_and_its_credential_helper_answers(tmp_path):
    """The ordinary-shell baseline every git test relies on; the check below runs it inside the coordinator's environment."""
    local = throwaway_repo(tmp_path)

    sh(local, "git", "push", "-q", "origin", "main", check=True)
    filled = sh(local, "git", "credential", "fill", input="protocol=https\nhost=example.com\n\n")

    assert sh(local, "git", "ls-remote", "--heads", "origin", "main").stdout.strip() != ""
    assert "password=hunter2" in filled.stdout


def test_the_baseline_holds_when_pytest_itself_runs_in_the_coordinators_check_environment(tmp_path):
    env = sanitize_env(dict(os.environ), [], str(ensure_shims(tmp_path / "state")))
    local = throwaway_repo(tmp_path)
    refused = sh(local, "git", "push", "-q", "origin", "main", env=env)
    assert refused.returncode != 0 and "DISABLED" in refused.stderr, "the environment under test really carries the guard"

    nested = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             f"{Path(__file__).relative_to(ROOT)}::test_a_test_owned_repository_pushes_to_its_origin_and_its_credential_helper_answers"],
                            cwd=ROOT, env=env, capture_output=True, text=True)

    assert nested.returncode == 0, nested.stdout + nested.stderr


def test_taking_the_guard_off_keeps_the_rest_of_the_check_environment():
    guarded = sanitize_env({"PATH": "/usr/bin", "HOME": "/h"}, [], "/shims")

    env = without_git_guard(guarded)

    assert not any(key == "GIT_CONFIG_COUNT" or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) for key in env)
    assert env == {key: value for key, value in guarded.items() if key in env}
    for kept in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT", "GH_CONFIG_DIR", "PATH", "HOME"):
        assert kept in env
