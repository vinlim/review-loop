import os

from review_loop.services.pytest_checks import pytest_check_env

PYTEST = ["/opt/venv/bin/python", "-m", "pytest", "-q"]


def test_a_pytest_check_leads_pythonpath_with_the_worktree_and_keeps_what_was_inherited(tmp_path):
    env = pytest_check_env(PYTEST, str(tmp_path), {"PATH": "/usr/bin", "PYTHONPATH": "/extra"})

    assert env["PYTHONPATH"] == os.pathsep.join([str(tmp_path), "/extra"]) and env["PATH"] == "/usr/bin"


def test_a_src_layout_worktree_comes_first_by_its_src_directory(tmp_path):
    (tmp_path / "src").mkdir()

    env = pytest_check_env(PYTEST, str(tmp_path), {})

    assert env["PYTHONPATH"] == os.pathsep.join([str(tmp_path / "src"), str(tmp_path)])


def test_other_checks_run_in_the_environment_as_given(tmp_path):
    env = {"PATH": "/usr/bin"}

    assert pytest_check_env([".claude/run-tests.sh", "changed"], str(tmp_path), env) is env


def test_the_import_roots_the_project_declares_to_pytest_lead_pythonpath_too(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["lib", "tests/helpers"]\n')

    env = pytest_check_env(PYTEST, str(tmp_path), {"PYTHONPATH": "/extra"})

    assert env["PYTHONPATH"] == os.pathsep.join([str(tmp_path / "lib"), str(tmp_path / "tests/helpers"), str(tmp_path), "/extra"])


def test_a_src_directory_the_config_already_declares_is_not_listed_twice(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = src\n")

    env = pytest_check_env(PYTEST, str(tmp_path), {})

    assert env["PYTHONPATH"] == os.pathsep.join([str(tmp_path / "src"), str(tmp_path)])
