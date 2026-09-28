"""Repository registration: detect what the project offers and write the trusted config table."""

from __future__ import annotations

import configparser
import json
import sys
import tomllib
from pathlib import Path

from review_loop.config.settings import DEFAULT_FORBIDDEN_TRAILERS
from review_loop.types.agents import AGENT_PROFILES

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
    if _configures_pytest(local_path):
        return [[_python_interpreter(local_path), "-m", "pytest", "-q"]]
    return []


def _configures_pytest(local_path: Path) -> bool:
    if (local_path / "pytest.ini").exists():
        return True
    pyproject = local_path / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text())
        except tomllib.TOMLDecodeError:
            data = {}
        if "ini_options" in data.get("tool", {}).get("pytest", {}):
            return True
    setup_cfg = local_path / "setup.cfg"
    if setup_cfg.exists():
        parser = configparser.ConfigParser()
        parser.read(setup_cfg)
        return parser.has_section("tool:pytest")
    return False


def _python_interpreter(local_path: Path) -> str:
    """Absolute, because the check runs in a tool worktree that has no .venv of its own."""
    venv_python = local_path / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def append_registration(config_path: Path, registration: str, state_dir: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(f"state_dir = {_v(str(state_dir))}\n\n{registration}")
        return
    existing = config_path.read_text()
    separator = "" if existing.endswith("\n\n") else "\n"
    config_path.write_text(existing + separator + registration)


def _v(value) -> str:
    """JSON is valid TOML for strings, integers, booleans and arrays of them."""
    return json.dumps(value, ensure_ascii=False)
