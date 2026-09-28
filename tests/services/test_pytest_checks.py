import os
import subprocess
import sys

import pytest

from review_loop.services.pytest_checks import pytest_check_command, pytest_check_env, pytest_options

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


MARKER_TEST = """import subprocess
import sys

import wtmarker


def test_direct_import():
    assert wtmarker.WHERE == "worktree"


def test_child_interpreter():
    out = subprocess.run([sys.executable, "-c", "import wtmarker; print(wtmarker.WHERE)"], capture_output=True, text=True)
    assert out.stdout.strip() == "worktree", out.stderr
"""

INI = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
LAYOUTS = {
    "flat root": ("", "pyproject.toml", INI),
    "conventional src": ("src", "pyproject.toml", INI),
    "custom root, TOML list": ("lib", "pyproject.toml", INI + 'pythonpath = ["lib"]\n'),
    "custom root, INI string": ("lib", "pytest.ini", "[pytest]\ntestpaths = tests\npythonpath = lib\n"),
    "quoted path with a space, INI string": ("src code", "pytest.ini", '[pytest]\ntestpaths = tests\npythonpath = "src code"\n'),
    "quoted path with a space, native TOML": ("src code", "pyproject.toml", '[tool.pytest]\ntestpaths = ["tests"]\npythonpath = ["src code"]\n'),
}


def checkout(root, layout, where):
    package = root / layout / "wtmarker"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'WHERE = "{where}"\n')
    return root / layout


@pytest.mark.parametrize("layout, config_file, config_text", LAYOUTS.values(), ids=LAYOUTS.keys())
def test_every_process_of_a_pytest_check_imports_the_worktree_ahead_of_an_inherited_main_checkout(tmp_path, layout, config_file, config_text):
    main_root = checkout(tmp_path / "main", layout, "main")
    worktree = tmp_path / "wt"
    checkout(worktree, layout, "worktree")
    (worktree / "tests").mkdir()
    (worktree / "tests" / "test_marker.py").write_text(MARKER_TEST)
    (worktree / config_file).write_text(config_text)
    inherited = os.pathsep.join([str(main_root), "/unrelated"])
    command = pytest_check_command(sys.executable, worktree, pytest_options(worktree))

    env = pytest_check_env(command, str(worktree), {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": inherited})
    completed = subprocess.run(command, cwd=worktree, env=env, capture_output=True, text=True)

    assert completed.returncode == 0 and "2 passed" in completed.stdout, completed.stdout + completed.stderr
    roots = env["PYTHONPATH"].split(os.pathsep)
    assert roots[-2:] == [str(main_root), "/unrelated"] and str(worktree) in roots[:-2]
    assert layout == "" or str(worktree / layout) == roots[0]
