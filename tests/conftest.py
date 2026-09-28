import os

import pytest

from review_loop.config.settings import load_settings
from tests.gitenv import without_git_guard

MINIMAL_TOML = '''
state_dir = "{state_dir}"

[repositories.webapp]
remote = "https://github.com/acme/webapp.git"
local_path = "{local_path}"
worktree_root = "{state_dir}/worktrees"
instruction_files = ["CLAUDE.md", "PROJECT.md"]
allowed_pr_authors = ["vinlim"]
trusted_logins = ["vinlim"]

[repositories.webapp.workspace]
prepare = [["bash", ".claude/worktree-setup.sh"]]
sanitize_env = ["DB_*", "DB_URL", "CACHE_STORE"]

[repositories.webapp.workspace.prepare_when_paths_match]
"^resources/(js|css)/" = [["bash", ".claude/worktree-setup.sh", "--js"]]

[repositories.webapp.verification]
required = [[".claude/run-tests.sh", "changed"]]
unavailable_exit_codes = [3]
'''


@pytest.fixture
def settings(tmp_path):
    local = tmp_path / "webapp"
    local.mkdir()
    path = tmp_path / "config.toml"
    path.write_text(MINIMAL_TOML.replace("{state_dir}", str(tmp_path / "state")).replace("{local_path}", str(local)))
    return load_settings(path)


@pytest.fixture(autouse=True, scope="session")
def ordinary_git_environment():
    """Run as a review-loop verification check, the suite inherits the agent environment, whose GIT_CONFIG_* entries
    disable pushes and credential helpers in every repository a process touches. The tests' own repositories expect an
    ordinary shell, and the guard under test is always built explicitly with sanitize_env, never inherited."""
    with pytest.MonkeyPatch.context() as patch:
        for key in set(os.environ) - set(without_git_guard(dict(os.environ))):
            patch.delenv(key)
        yield
