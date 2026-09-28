from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RunState(StrEnum):
    PREPARING = "preparing"
    REVIEWING = "reviewing"
    ASSESSING = "assessing"
    FIXING = "fixing"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"
    REREVIEWING = "rereviewing"
    ALIGNING = "aligning"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


# States a coordinator process drives; the rest wait for a person, or are over.
WORKING_STATES = frozenset(state for state in RunState if state not in (RunState.PAUSED, RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED))


class PauseReason(StrEnum):
    USAGE_LIMIT = "usage_limit"
    AUTH_REQUIRED = "auth_required"
    HEAD_CHANGED = "head_changed"
    WORKSPACE_DIRTY = "workspace_dirty"
    CHECKS_FAILED = "checks_failed"
    PREPARE_FAILED = "prepare_failed"
    AGENT_FAILED = "agent_failed"
    INSPECT_ONLY = "inspect_only"
    UNEXPECTED_COMMIT = "unexpected_commit"
    PUSH_FAILED = "push_failed"
    SCRIPTS_CHANGED = "scripts_changed"
    WORKSPACE_FOREIGN = "workspace_foreign"
    GITHUB_ERROR = "github_error"
    READ_ONLY_VIOLATED = "read_only_violated"
    COORDINATOR_FAILED = "coordinator_failed"
    MANUAL = "manual"


class Outcome(StrEnum):
    COMPLETE = "complete"
    COMPLETE_WITH_EXCEPTIONS = "complete_with_exceptions"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Budgets:
    max_review_passes: int
    max_fix_attempts: int
    max_alignment_exchanges: int


@dataclass(frozen=True)
class AgentChoice:
    """Which CLI a role runs and the model and effort it was given; empty model or effort means the CLI chose."""

    agent: str
    model: str
    effort: str


@dataclass
class Run:
    id: str
    repo: str
    pr_number: int
    pr_url: str
    pr_author: str
    head_ref: str
    base_ref: str
    head_sha: str
    base_sha: str
    merge_base_sha: str
    state: RunState
    budgets: Budgets
    versions: dict[str, str]
    worktree_path: str = ""
    local_branch: str = ""
    author_session: str = ""
    pass_no: int = 0
    pause_reason: PauseReason | None = None
    resume_state: RunState | None = None
    outcome: Outcome | None = None
    created_at: str = ""
    updated_at: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    agents: dict[str, AgentChoice] = field(default_factory=dict)

    def mode(self) -> str:
        """`inspect` or `publish`, stored at enrolment; without it, a run counts as inspect only while paused for inspection."""
        stored = self.extra.get("mode")
        if stored:
            return stored
        return "inspect" if self.state == RunState.PAUSED and self.pause_reason == PauseReason.INSPECT_ONLY else "publish"
