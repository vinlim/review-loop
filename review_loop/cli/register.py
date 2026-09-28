"""Repository registration: detect what the project offers and write the trusted config table."""

from __future__ import annotations

import configparser
import json
import sys
import tomllib
from pathlib import Path

from review_loop.config.settings import DEFAULT_FORBIDDEN_TRAILERS
from review_loop.services.state_dir import ensure_private_dir
from review_loop.types.agents import AGENT_PROFILES

PYTEST_CONFIG_FILES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
LARAVEL_SANITIZED_ENV = ["DB_*", "DB_URL", "CACHE_STORE", "SESSION_DRIVER", "QUEUE_CONNECTION", "BROADCAST_CONNECTION", "MAIL_MAILER"]


def registration_toml(name: str, local_path: Path, remote: str, worktree_root: Path, author: str) -> str:
    has_setup = (local_path / ".claude" / "worktree-setup.sh").exists()
    has_runner = (local_path / ".claude" / "run-tests.sh").exists()
    prepare = [["bash", ".claude/worktree-setup.sh"]] if has_setup else []
    required = _required_checks(local_path, has_runner)
    format_commands = [["vendor/bin/pint", "--dirty", "--format", "agent"]] if (local_path / "pint.json").exists() else []
    instruction_files = [f for f in ("CLAUDE.md", "AGENTS.md", "PROJECT.md") if (local_path / f).exists()]
    sanitize = LARAVEL_SANITIZED_ENV if (local_path / "artisan").exists() else []
    lines = [
        f"[repositories.{name}]",
        f"remote = {_v(remote)}",
        f"local_path = {_v(str(local_path))}",
        f"worktree_root = {_v(str(worktree_root))}",
        f"instruction_files = {_v(instruction_files)}",
        f"allowed_pr_authors = {_v([author])}",
        f"trusted_logins = {_v([author])}",
        "",
        f"[repositories.{name}.workspace]",
        f"prepare = {_v(prepare)}",
        f"sanitize_env = {_v(sanitize)}",
    ]
    if has_setup and (local_path / "resources" / "js").exists():
        lines += ["", f"[repositories.{name}.workspace.prepare_when_paths_match]",
                  f'"^resources/(js|css)/" = {_v([["bash", ".claude/worktree-setup.sh", "--js"]])}']
    lines += [
        "",
        f"[repositories.{name}.verification]",
        f"required = {_v(required)}",
        f"unavailable_exit_codes = {_v([3] if has_runner else [])}",
        f"format = {_v(format_commands)}",
        "",
        f"[repositories.{name}.review]",
        f"# Agents: {', '.join(sorted(AGENT_PROFILES))}. An unset model or effort uses the agent's default.",
        'reviewer = "codex"',
        'author = "claude"',
        "max_review_passes = 7",
        "max_fix_attempts = 2",
        "max_alignment_exchanges = 1",
        "",
        f"[repositories.{name}.publication]",
        "post_reviews = true",
        "post_author_responses = true",
        "push_verified_fixes = true",
        "mirror_inbox_in_pr_comment = true",
        f"forbid_commit_trailers = {_v(DEFAULT_FORBIDDEN_TRAILERS)}",
        "",
    ]
    return "\n".join(lines)


def _required_checks(local_path: Path, has_runner: bool) -> list[list[str]]:
    """The project's own runner wins; a Python project that configures pytest gets pytest."""
    if has_runner:
        return [[".claude/run-tests.sh", "changed"]]
    options = _pytest_options(local_path)
    if options is None:
        return []
    return [_pytest_command(local_path, options)]


def _pytest_command(local_path: Path, options: dict) -> list[str]:
    """Runnable by hand from a worktree: `python -m pytest` puts the cwd first on sys.path, and a src layout gets src
    put there too. In a run the coordinator also leads PYTHONPATH with the worktree, so child processes agree."""
    command = [_python_interpreter(local_path), "-m", "pytest", "-q"]
    configured = _paths(options.get("pythonpath", []))
    if (local_path / "src").is_dir() and "src" not in configured:
        command += ["-o", "pythonpath=" + " ".join(["src", *configured])]
    return command


def _pytest_options(local_path: Path) -> dict | None:
    """The options of the first file pytest itself would read, in its order; None when none configures pytest."""
    for name in PYTEST_CONFIG_FILES:
        path = local_path / name
        if path.exists():
            options = _pytest_options_in(path)
            if options is not None:
                return options
    return None


def _pytest_options_in(path: Path) -> dict | None:
    if path.name in ("pytest.ini", ".pytest.ini"):
        return _ini_section(path, "pytest") or {}
    if path.name in ("pytest.toml", ".pytest.toml"):
        return _toml(path).get("pytest", {})
    if path.name == "pyproject.toml":
        table = _toml(path).get("tool", {}).get("pytest")
        if not isinstance(table, dict):
            return None
        return {**{key: value for key, value in table.items() if key != "ini_options"}, **table.get("ini_options", {})}
    return _ini_section(path, "tool:pytest" if path.name == "setup.cfg" else "pytest")


def _ini_section(path: Path, section: str) -> dict | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path)
    except configparser.Error:
        return None
    return dict(parser[section]) if parser.has_section(section) else None


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError:
        return {}


def _paths(value) -> list[str]:
    return value.split() if isinstance(value, str) else [str(entry) for entry in value]


def _python_interpreter(local_path: Path) -> str:
    """Absolute, because the check runs in a tool worktree that has no .venv of its own."""
    venv_python = local_path / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def append_registration(config_path: Path, registration: str, state_dir: Path) -> None:
    ensure_private_dir(config_path.parent)
    if not config_path.exists():
        config_path.write_text(f"state_dir = {_v(str(state_dir))}\n\n{registration}")
        return
    existing = config_path.read_text()
    separator = "" if existing.endswith("\n\n") else "\n"
    config_path.write_text(existing + separator + registration)


def _v(value) -> str:
    """JSON is valid TOML for strings, integers, booleans and arrays of them."""
    return json.dumps(value, ensure_ascii=False)
