"""The composition root: the one place concrete adapters are built and handed to services."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from review_loop.adapters.git_cli import GitCli
from review_loop.adapters.github_gh import GhGitHub
from review_loop.adapters.process import SubprocessRunner
from review_loop.config.settings import Settings, load_settings
from review_loop.repositories.db import connect, migrate
from review_loop.services.state_dir import ensure_private_dir

TOOL_VERSION = "0.1.0"


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class Container:
    settings: Settings
    conn: sqlite3.Connection
    process: Any
    git: Any
    github: Any
    clock: Any
    versions: dict[str, str] = field(default_factory=dict)
    home: Path = Path.home() / ".review-loop"
    claude_oauth_token: str = ""  # the Claude adapter is its only holder; every other subprocess starts without it

    @property
    def schemas_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / "schemas"

    @property
    def prompts_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / "prompts"


def default_home() -> Path:
    return Path(os.environ.get("REVIEW_LOOP_HOME", str(Path.home() / ".review-loop")))


def claim_claude_oauth_token() -> str:
    """Taken out of the process environment before anything else runs, so no subprocess of the coordinator inherits it."""
    return os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", "")


def build_container(home: Path | None = None, claude_oauth_token: str = "") -> Container:
    home = home or default_home()
    settings = load_settings(home / "config.toml")
    ensure_private_dir(settings.state_dir)
    conn = connect(str(settings.state_dir / "state.db"))
    migrate(conn)
    process = SubprocessRunner()
    env = dict(os.environ)
    return Container(settings=settings, conn=conn, process=process, git=GitCli(process, env),
                     github=GhGitHub(process, cwd=str(home), env=env), clock=SystemClock(),
                     versions=tool_versions(process, env), home=home, claude_oauth_token=claude_oauth_token)


def tool_versions(process: SubprocessRunner, env: dict[str, str]) -> dict[str, str]:
    versions = {"review-loop": TOOL_VERSION}
    for tool in ("claude", "codex", "gh"):
        completed = process.run([tool, "--version"], cwd=str(Path.home()), env=env, timeout_seconds=30)
        versions[tool] = completed.stdout.strip().splitlines()[0] if completed.exit_code == 0 and completed.stdout.strip() else "missing"
    return versions
