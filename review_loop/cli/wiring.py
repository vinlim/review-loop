"""Build the phase dependencies from the container: the only place the agent adapters are chosen."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from review_loop.adapters.agents.claude import ClaudeAdapter
from review_loop.adapters.agents.codex import CodexAdapter
from review_loop.adapters.claude_sessions import find_author_session
from review_loop.adapters.notify.macos import MacNotifier
from review_loop.adapters.notify.stdout import StdoutNotifier
from review_loop.cli.container import Container
from review_loop.services.phase_support import Deps


def build_deps(box: Container, inspect_only: bool, platform: str = sys.platform, claude_projects_dir: Path | None = None) -> Deps:
    projects = claude_projects_dir or Path.home() / ".claude" / "projects"
    notifier = MacNotifier(box.process) if platform == "darwin" else StdoutNotifier()
    return Deps(
        settings=box.settings, conn=box.conn, git=box.git, github=box.github, process=box.process, clock=box.clock,
        reviewer=CodexAdapter(box.process), author=ClaudeAdapter(box.process, oauth_token=box.claude_oauth_token),
        prompts_dir=box.prompts_dir, schemas_dir=box.schemas_dir,
        base_env=dict(os.environ), runs_dir=box.settings.state_dir / "runs",
        find_author_session=lambda branch, local_path="": find_author_session(projects, branch, local_path), inspect_only=inspect_only, notifier=notifier,
    )
