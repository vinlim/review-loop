"""Enrol a pull request: one active run per PR, only for registered repositories and allowed authors."""

from __future__ import annotations

import sqlite3
from enum import StrEnum

from review_loop.config.settings import RepositoryConfig, Settings
from review_loop.engine.pull_url import parse_pull_url
from review_loop.repositories import runs as runs_repo
from review_loop.types.agents import AGENT_PROFILES
from review_loop.types.protocols import Clock, GitClient, GitHubGateway
from review_loop.types.pull_request import PullRef
from review_loop.types.result import Err, Ok, Result
from review_loop.types.run import Budgets, Run, RunState


class StartRefusal(StrEnum):
    REPOSITORY_NOT_REGISTERED = "repository_not_registered"
    AUTHOR_NOT_ALLOWED = "author_not_allowed"
    PULL_NOT_OPEN = "pull_not_open"
    FORK_NOT_SUPPORTED = "fork_not_supported"
    AUTHOR_SESSION_NOT_FORKABLE = "author_session_not_forkable"


def start_run(url: str, *, settings: Settings, conn: sqlite3.Connection, github: GitHubGateway, git: GitClient,
              clock: Clock, versions: dict[str, str], author_session: str = "auto", inspect_only: bool = False) -> Result[Run, StartRefusal]:
    ref = parse_pull_url(url)
    repo = find_repository(settings, ref)
    if repo is None:
        return Err(StartRefusal.REPOSITORY_NOT_REGISTERED)
    if author_session not in ("", "auto") and not AGENT_PROFILES[repo.review.author].forks_sessions:
        return Err(StartRefusal.AUTHOR_SESSION_NOT_FORKABLE)
    active = runs_repo.find_active_run(conn, repo.name, ref.number)
    if active is not None:
        return Ok(active)
    pull = github.fetch_pull(ref)
    if pull.author not in repo.allowed_pr_authors:
        return Err(StartRefusal.AUTHOR_NOT_ALLOWED)
    if pull.state != "open":
        return Err(StartRefusal.PULL_NOT_OPEN)
    if pull.head_repo and pull.head_repo.lower() != f"{ref.owner}/{ref.repo}".lower():
        return Err(StartRefusal.FORK_NOT_SUPPORTED)
    git.fetch(str(repo.local_path), "origin", [pull.base_ref, pull.head_ref])
    now = clock.now()
    run = Run(
        id=f"{repo.name}-{ref.number}-{now.strftime('%Y%m%d-%H%M%S')}",
        repo=repo.name, pr_number=ref.number, pr_url=ref.url, pr_author=pull.author,
        head_ref=pull.head_ref, base_ref=pull.base_ref, head_sha=pull.head_sha, base_sha=pull.base_sha,
        merge_base_sha=git.merge_base(str(repo.local_path), pull.base_sha, pull.head_sha),
        state=RunState.PREPARING,
        budgets=Budgets(repo.review.max_review_passes, repo.review.max_fix_attempts, repo.review.max_alignment_exchanges),
        versions=dict(versions), author_session=author_session, created_at=now.isoformat(), updated_at=now.isoformat(),
        extra={"mode": "inspect" if inspect_only else "publish", "remote_head": pull.head_sha},
        agents=repo.review.agents(),
    )
    runs_repo.create_run(conn, run)
    return Ok(run)


def find_repository(settings: Settings, ref: PullRef) -> RepositoryConfig | None:
    wanted = f"{ref.owner}/{ref.repo}".lower()
    for repo in settings.repositories.values():
        if _owner_and_name(repo.remote) == wanted:
            return repo
    return None


def _owner_and_name(remote: str) -> str:
    """owner/name from an HTTPS or SSH GitHub remote, compared exactly rather than by substring."""
    tail = remote.strip().removesuffix(".git").replace("git@github.com:", "github.com/")
    parts = [part for part in tail.split("/") if part]
    return "/".join(parts[-2:]).lower() if len(parts) >= 2 else ""
