"""A registered pytest check: what a project declares to pytest, where its imports come from, and the environment
the check runs in so that every process it starts imports the worktree rather than an installed copy of the project.

The config comes from the reviewed worktree, so the parser never raises: a value it cannot read declares nothing and a
file it cannot read configures nothing. pytest reads the same files when the check runs and is the judge of them;
it accepts some shapes this parser does not, and reports the rest in the verification log."""

from __future__ import annotations

import configparser
import os
import shlex
import tomllib
from pathlib import Path

PYTEST_CONFIG_FILES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")


def pytest_options(project: Path) -> dict | None:
    """The options of the first file pytest itself would read, in its order; None when none configures pytest."""
    for name in PYTEST_CONFIG_FILES:
        path = project / name
        if path.exists():
            options = _options_in(path)
            if options is not None:
                return options
    return None


def import_roots(project: Path, options: dict) -> list[str]:
    """Where the project's imports come from, relative to it: src/ when it has one, then what its pytest config declares."""
    configured = _paths(options.get("pythonpath", []))
    src = ["src"] if (project / "src").is_dir() and "src" not in configured else []
    return src + configured


def pytest_check_command(python: str, project: Path, options: dict) -> list[str]:
    """Runnable by hand from a worktree: `python -m pytest` puts the cwd first on sys.path, and pytest reads the
    project's own pythonpath, so only a src/ the config does not declare needs adding."""
    command = [python, "-m", "pytest", "-q"]
    roots = import_roots(project, options)
    if roots != _paths(options.get("pythonpath", [])):
        command += ["-o", "pythonpath=" + shlex.join(roots)]
    return command


def is_pytest_check(command: list[str]) -> bool:
    return command[1:3] == ["-m", "pytest"]


def pytest_check_env(command: list[str], worktree: str, env: dict[str, str]) -> dict[str, str]:
    """PYTHONPATH leads with the worktree's import roots and the worktree itself, then whatever was inherited, so the
    processes a pytest check starts resolve the project the way pytest's own process does."""
    if not is_pytest_check(command):
        return env
    root = Path(worktree)
    leading = [str(root / entry) for entry in import_roots(root, pytest_options(root) or {})] + [str(root)]
    inherited = env.get("PYTHONPATH", "")
    return {**env, "PYTHONPATH": os.pathsep.join(leading + ([inherited] if inherited else []))}


def _options_in(path: Path) -> dict | None:
    if path.name in ("pytest.ini", ".pytest.ini"):
        return _ini_section(path, "pytest") or {}
    if path.name in ("pytest.toml", ".pytest.toml"):
        return _table(_toml(path).get("pytest")) or {}
    if path.name == "pyproject.toml":
        tool = _table(_toml(path).get("tool")) or {}
        table = _table(tool.get("pytest"))
        if table is None:
            return None
        ini_options = _table(table.get("ini_options")) or {}
        return {**{key: value for key, value in table.items() if key != "ini_options"}, **ini_options}
    return _ini_section(path, "tool:pytest" if path.name == "setup.cfg" else "pytest")


def _ini_section(path: Path, section: str) -> dict | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path)
    except (configparser.Error, UnicodeError):
        return None
    return dict(parser[section]) if parser.has_section(section) else None


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, UnicodeError):
        return {}


def _table(value) -> dict | None:
    return value if isinstance(value, dict) else None


def _paths(value) -> list[str]:
    """As pytest reads a `paths` option: shell quoting for a string, a list as given, nothing for anything else."""
    if isinstance(value, list):
        return [str(entry) for entry in value]
    if not isinstance(value, str):
        return []
    try:
        return shlex.split(value)
    except ValueError:
        return []
