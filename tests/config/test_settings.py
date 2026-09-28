import pytest

from review_loop.config.settings import (
    DEFAULT_FORBIDDEN_TRAILERS,
    DEFAULT_TIMEOUTS_MINUTES,
    ConfigError,
    PublicationConfig,
    ReviewConfig,
    load_settings,
)

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


def with_repository_line(line):
    return MINIMAL.replace('trusted_logins = ["vinlim"]\n', f'trusted_logins = ["vinlim"]\n{line}\n')


def test_valid_toml_loads_into_typed_settings_with_defaults_filled_in(tmp_path):
    settings = load_settings(write(tmp_path, MINIMAL))

    repo = settings.repositories["webapp"]
    assert repo.remote == "https://github.com/acme/webapp.git"
    assert repo.workspace.prepare == [["bash", ".claude/worktree-setup.sh"]]
    assert repo.verification.unavailable_exit_codes == [3]
    assert repo.review.max_review_passes == 7
    assert repo.review.max_fix_attempts == 2
    assert repo.review.max_alignment_exchanges == 1
    assert repo.review.reviewer_model == "gpt-5.6-sol"
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


def test_the_reviewer_is_codex_and_the_author_is_claude_unless_configured(tmp_path):
    review = load_settings(write(tmp_path, MINIMAL)).repositories["webapp"].review

    assert (review.reviewer, review.reviewer_model, review.reviewer_effort) == ("codex", "gpt-5.6-sol", "xhigh")
    assert (review.author, review.author_model, review.author_effort) == ("claude", "claude-opus-5-5", "xhigh")


def test_another_agent_without_a_model_leaves_model_and_effort_to_the_cli(tmp_path):
    text = MINIMAL + '\n[repositories.webapp.review]\nreviewer = "agy"\nauthor = "opencode"\nauthor_model = "anthropic/claude-fable-5-1"\n'

    review = load_settings(write(tmp_path, text)).repositories["webapp"].review

    assert (review.reviewer, review.reviewer_model, review.reviewer_effort) == ("agy", "", "")
    assert (review.author, review.author_model, review.author_effort) == ("opencode", "anthropic/claude-fable-5-1", "")


def test_an_unknown_agent_is_rejected_naming_the_key_and_the_known_agents(tmp_path):
    path = write(tmp_path, MINIMAL + '\n[repositories.webapp.review]\nauthor = "gpt-pilot"\n')

    with pytest.raises(ConfigError) as raised:
        load_settings(path)

    assert "repositories.webapp.review.author" in str(raised.value)
    assert "agy, claude, codex, opencode" in str(raised.value)


@pytest.mark.parametrize("value", ['["codex"]', '{ name = "claude" }'])
def test_an_agent_given_as_an_array_or_table_is_rejected_naming_the_key(tmp_path, value):
    path = write(tmp_path, MINIMAL + f"\n[repositories.webapp.review]\nauthor = {value}\n")

    with pytest.raises(ConfigError) as raised:
        load_settings(path)

    assert "repositories.webapp.review.author must be a string" in str(raised.value)


def test_every_review_and_publication_key_overrides_its_default(tmp_path):
    text = MINIMAL + '''
[repositories.webapp.review]
reviewer = "claude"
author = "codex"
reviewer_model = "claude-fable-5-1"
reviewer_effort = "high"
author_model = "gpt-5.6-mini"
author_effort = "medium"
max_review_passes = 3
max_fix_attempts = 1
max_alignment_exchanges = 0

[repositories.webapp.review.timeouts_minutes]
fix = 90

[repositories.webapp.publication]
post_reviews = false
post_author_responses = false
push_verified_fixes = false
mirror_inbox_in_pr_comment = false
forbid_commit_trailers = ["Signed-off-by"]
'''

    repo = load_settings(write(tmp_path, text)).repositories["webapp"]

    assert repo.review == ReviewConfig(
        max_review_passes=3, max_fix_attempts=1, max_alignment_exchanges=0,
        reviewer="claude", author="codex", reviewer_model="claude-fable-5-1", reviewer_effort="high",
        author_model="gpt-5.6-mini", author_effort="medium", timeouts_minutes={**DEFAULT_TIMEOUTS_MINUTES, "fix": 90})
    assert repo.publication == PublicationConfig(False, False, False, False, ["Signed-off-by"])


def test_a_misspelled_review_key_is_rejected_naming_the_key_the_file_and_the_key_it_resembles(tmp_path):
    path = write(tmp_path, MINIMAL + '\n[repositories.webapp.review]\nreviwer = "agy"\n')

    with pytest.raises(ConfigError) as raised:
        load_settings(path)

    assert "repositories.webapp.review.reviwer is not a known key (did you mean reviewer?)" in str(raised.value)
    assert str(path) in str(raised.value)


def test_an_unknown_publication_key_resembling_none_is_rejected_listing_the_known_keys(tmp_path):
    path = write(tmp_path, MINIMAL + '\n[repositories.webapp.publication]\nnotify_slack = true\n')

    with pytest.raises(ConfigError) as raised:
        load_settings(path)

    assert "repositories.webapp.publication.notify_slack is not a known key" in str(raised.value)
    assert "forbid_commit_trailers, mirror_inbox_in_pr_comment, post_author_responses, post_reviews, push_verified_fixes" in str(raised.value)


def test_an_unknown_timeout_phase_is_rejected_naming_the_phase_it_resembles(tmp_path):
    path = write(tmp_path, MINIMAL + '\n[repositories.webapp.review.timeouts_minutes]\nfixx = 90\n')

    with pytest.raises(ConfigError) as raised:
        load_settings(path)

    assert "repositories.webapp.review.timeouts_minutes.fixx is not a known key (did you mean fix?)" in str(raised.value)


@pytest.mark.parametrize("line, key, problem", [
    ("review = 5", "review", "must be a table"),
    ("review = { reviewer_model = 5 }", "review.reviewer_model", "must be a string"),
    ("review = { author_effort = false }", "review.author_effort", "must be a string"),
    ("review = { max_review_passes = \"7\" }", "review.max_review_passes", "must be an integer"),
    ("review = { max_fix_attempts = true }", "review.max_fix_attempts", "must be an integer"),
    ("review = { max_alignment_exchanges = 1.5 }", "review.max_alignment_exchanges", "must be an integer"),
    ("review = { timeouts_minutes = 60 }", "review.timeouts_minutes", "must be a table"),
    ("review = { timeouts_minutes = { fix = \"60\" } }", "review.timeouts_minutes.fix", "must be an integer"),
    ("publication = []", "publication", "must be a table"),
    ("publication = { post_reviews = \"yes\" }", "publication.post_reviews", "must be true or false"),
    ("publication = { push_verified_fixes = 1 }", "publication.push_verified_fixes", "must be true or false"),
    ("publication = { forbid_commit_trailers = \"Co-Authored-By\" }", "publication.forbid_commit_trailers", "must be an array of strings"),
])
def test_a_review_or_publication_value_of_the_wrong_type_is_rejected_naming_the_key_and_the_file(tmp_path, line, key, problem):
    path = write(tmp_path, with_repository_line(line))

    with pytest.raises(ConfigError) as raised:
        load_settings(path)

    assert f"repositories.webapp.{key} {problem}" in str(raised.value)
    assert str(path) in str(raised.value)
