import sys
import tomllib

from review_loop.cli.register import append_registration, registration_toml
from review_loop.config.settings import load_settings


def project(tmp_path, with_scripts=True):
    repo = tmp_path / "webapp"
    (repo / ".claude").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("rules")
    (repo / "PROJECT.md").write_text("context")
    if with_scripts:
        (repo / ".claude" / "worktree-setup.sh").write_text("#!/bin/sh\n")
        (repo / ".claude" / "run-tests.sh").write_text("#!/bin/sh\n")
    return repo


def test_registration_detects_the_project_scripts_and_instruction_files(tmp_path):
    repo = project(tmp_path)

    text = registration_toml("webapp", repo, "https://github.com/acme/webapp.git", tmp_path / "wt", "vinlim")

    table = tomllib.loads(text)["repositories"]["webapp"]
    assert table["workspace"]["prepare"] == [["bash", ".claude/worktree-setup.sh"]]
    assert table["verification"]["required"] == [[".claude/run-tests.sh", "changed"]]
    assert table["verification"]["unavailable_exit_codes"] == [3]
    assert table["instruction_files"] == ["CLAUDE.md", "PROJECT.md"]
    assert table["allowed_pr_authors"] == ["vinlim"] and table["trusted_logins"] == ["vinlim"]


def test_registration_names_the_default_agents_so_the_choice_is_visible(tmp_path):
    text = registration_toml("webapp", project(tmp_path), "https://github.com/acme/webapp.git", tmp_path / "wt", "vinlim")

    review = tomllib.loads(text)["repositories"]["webapp"]["review"]
    assert (review["reviewer"], review["author"]) == ("codex", "claude")
    assert "agy, claude, codex, opencode" in text


def test_registration_registers_pytest_for_a_python_project_that_configures_it(tmp_path):
    repo = project(tmp_path, with_scripts=False)
    (repo / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')

    text = registration_toml("tool", repo, "https://github.com/x/tool.git", tmp_path / "wt", "vinlim")

    verification = tomllib.loads(text)["repositories"]["tool"]["verification"]
    assert verification["required"] == [[sys.executable, "-m", "pytest", "-q"]]
    assert verification["unavailable_exit_codes"] == []


def test_registration_prefers_the_project_venv_interpreter_when_it_exists(tmp_path):
    repo = project(tmp_path, with_scripts=False)
    (repo / "pytest.ini").write_text("[pytest]\n")
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")

    text = registration_toml("tool", repo, "https://github.com/x/tool.git", tmp_path / "wt", "vinlim")

    assert tomllib.loads(text)["repositories"]["tool"]["verification"]["required"] == [[str(repo / ".venv" / "bin" / "python"), "-m", "pytest", "-q"]]


def test_registration_detects_pytest_in_setup_cfg_but_not_a_pyproject_without_it(tmp_path):
    repo = project(tmp_path, with_scripts=False)
    (repo / "pyproject.toml").write_text('[project]\nname = "tool"\n')
    assert tomllib.loads(registration_toml("tool", repo, "r", tmp_path / "wt", "v"))["repositories"]["tool"]["verification"]["required"] == []

    (repo / "setup.cfg").write_text("[tool:pytest]\ntestpaths = tests\n")

    assert tomllib.loads(registration_toml("tool", repo, "r", tmp_path / "wt", "v"))["repositories"]["tool"]["verification"]["required"] != []


def test_the_project_test_runner_wins_over_pytest_detection(tmp_path):
    repo = project(tmp_path)
    (repo / "pytest.ini").write_text("[pytest]\n")

    text = registration_toml("webapp", repo, "https://github.com/acme/webapp.git", tmp_path / "wt", "vinlim")

    assert tomllib.loads(text)["repositories"]["webapp"]["verification"]["required"] == [[".claude/run-tests.sh", "changed"]]


def test_registration_without_known_scripts_leaves_empty_arrays_for_the_developer_to_fill(tmp_path):
    repo = project(tmp_path, with_scripts=False)

    text = registration_toml("other", repo, "https://github.com/x/other.git", tmp_path / "wt", "vinlim")

    table = tomllib.loads(text)["repositories"]["other"]
    assert table["workspace"]["prepare"] == [] and table["verification"]["required"] == []


def test_append_registration_creates_the_config_then_adds_a_second_repository(tmp_path):
    config = tmp_path / "config.toml"
    first = registration_toml("webapp", project(tmp_path), "https://github.com/acme/webapp.git", tmp_path / "wt", "vinlim")

    append_registration(config, first, state_dir=tmp_path / "state")
    second = registration_toml("other", project(tmp_path / "second", with_scripts=False), "https://github.com/x/other.git", tmp_path / "wt", "vinlim")
    append_registration(config, second, state_dir=tmp_path / "state")

    settings = load_settings(config)
    assert set(settings.repositories) == {"webapp", "other"}
    assert settings.state_dir == tmp_path / "state"
