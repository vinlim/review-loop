import pytest

from review_loop.config.settings import DEFAULT_FORBIDDEN_TRAILERS, ConfigError, load_settings

MINIMAL = '''
state_dir = "{state_dir}"

[repositories.webapp]
remote = "https://github.com/acme/webapp.git"
local_path = "/home/dev/webapp"
worktree_root = "{state_dir}/worktrees"
instruction_files = ["CLAUDE.md", "PROJECT.md"]
allowed_pr_authors = ["vinlim"]
trusted_logins = ["vinlim"]

[repositories.webapp.workspace]
prepare = [["bash", ".claude/worktree-setup.sh"]]

[repositories.webapp.verification]
required = [[".claude/run-tests.sh", "changed"]]
unavailable_exit_codes = [3]
'''


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text.replace("{state_dir}", str(tmp_path / "state")))
    return path


def test_valid_toml_loads_into_typed_settings_with_defaults_filled_in(tmp_path):
    settings = load_settings(write(tmp_path, MINIMAL))

    repo = settings.repositories["webapp"]
    assert repo.remote == "https://github.com/acme/webapp.git"
    assert repo.workspace.prepare == [["bash", ".claude/worktree-setup.sh"]]
    assert repo.verification.unavailable_exit_codes == [3]
    assert repo.review.max_review_passes == 7
    assert repo.review.max_fix_attempts == 2
    assert repo.review.max_alignment_exchanges == 1
    assert repo.review.reviewer_model == "gpt-6-astra"
    assert repo.review.timeouts_minutes["fix"] == 60
    assert repo.publication.forbid_commit_trailers == DEFAULT_FORBIDDEN_TRAILERS
    assert settings.state_dir == tmp_path / "state"


def test_missing_required_key_raises_naming_the_key_and_the_file(tmp_path):
    path = write(tmp_path, MINIMAL.replace('local_path = "/home/dev/webapp"\n', ""))

    with pytest.raises(ConfigError) as raised:
        load_settings(path)

    assert "repositories.webapp.local_path" in str(raised.value)
    assert str(path) in str(raised.value)


def test_command_given_as_a_string_instead_of_an_argument_array_is_rejected(tmp_path):
    path = write(tmp_path, MINIMAL.replace('prepare = [["bash", ".claude/worktree-setup.sh"]]', 'prepare = "bash .claude/worktree-setup.sh"'))

    with pytest.raises(ConfigError) as raised:
        load_settings(path)

    assert "repositories.webapp.workspace.prepare" in str(raised.value)
    assert "array" in str(raised.value)
