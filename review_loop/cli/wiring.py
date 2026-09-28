"""Build the phase dependencies from the container: the only place the agent adapters are chosen."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from review_loop.adapters.agents.agy import AgyAdapter
from review_loop.adapters.agents.claude import ClaudeAdapter
from review_loop.adapters.agents.codex import CodexAdapter
from review_loop.adapters.agents.opencode import OpencodeAdapter
from review_loop.adapters.claude_sessions import find_author_session
from review_loop.adapters.notify.macos import MacNotifier
from review_loop.adapters.notify.stdout import StdoutNotifier
from review_loop.cli.container import Container
from review_loop.services.phase_support import Deps

AGENT_ADAPTERS = {"agy": AgyAdapter, "claude": ClaudeAdapter, "codex": CodexAdapter, "opencode": OpencodeAdapter}


def _desktop_session(projects: Path, agent: str, branch: str, local_path: str) -> str:
    """Only Claude Code's desktop sessions can be found on disk; other authors start without one."""
    return find_author_session(projects, branch, local_path) if agent == "claude" else ""


def _adapters(box: Container) -> dict[str, object]:
    """One adapter per agent the config accepts; the claimed Claude login goes to the Claude adapter alone."""
    if box.agents is not None:
        return dict(box.agents)
    adapters = {name: adapter(box.process) for name, adapter in AGENT_ADAPTERS.items()}
    adapters["claude"] = ClaudeAdapter(box.process, oauth_token=box.claude_oauth_token)
    return adapters


def build_deps(box: Container, inspect_only: bool, platform: str = sys.platform, claude_projects_dir: Path | None = None) -> Deps:
    projects = claude_projects_dir or Path.home() / ".claude" / "projects"
    notifier = MacNotifier(box.process) if platform == "darwin" else StdoutNotifier()
    return Deps(
        settings=box.settings, conn=box.conn, git=box.git, github=box.github, process=box.process, clock=box.clock,
        agents=_adapters(box), prompts_dir=box.prompts_dir, schemas_dir=box.schemas_dir,
        base_env=dict(os.environ), runs_dir=box.settings.state_dir / "runs",
        find_author_session=lambda agent, branch, local_path="": _desktop_session(projects, agent, branch, local_path),
        inspect_only=inspect_only, notifier=notifier,
    )
