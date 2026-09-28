from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentFailure(StrEnum):
    PROCESS_FAILED = "process_failed"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    SCHEMA_VIOLATION = "schema_violation"
    USAGE_LIMIT = "usage_limit"
    AUTH_REQUIRED = "auth_required"


@dataclass(frozen=True)
class AgentProfile:
    """What the coordinator needs to know about an agent CLI before it has an adapter in hand.

    An empty default model or effort leaves the choice to the CLI. `forks_sessions` means resuming a session
    never appends to it, which is what makes resuming the developer's own session safe."""

    binary: str
    install_hint: str
    default_model: str = ""
    default_effort: str = ""
    forks_sessions: bool = False


AGENT_PROFILES = {
    "agy": AgentProfile("agy", "install the Antigravity CLI, then run `agy` once to sign in"),
    "claude": AgentProfile("claude", "install Claude Code, then run `claude` once to log in", "claude-fable-5-1", "high",
                           forks_sessions=True),
    "codex": AgentProfile("codex", "install with `npm i -g @openai/codex`, then run `codex login status`", "gpt-6-astra", "ultra"),
    "opencode": AgentProfile("opencode", "install with `npm i -g opencode-ai`, then run `opencode auth login`", forks_sessions=True),
}


@dataclass(frozen=True)
class AgentError:
    kind: AgentFailure
    detail: str


@dataclass(frozen=True)
class PhaseRequest:
    phase: str
    prompt: str
    schema_path: str
    cwd: str
    env: dict[str, str]
    timeout_seconds: int
    model: str
    effort: str
    output_dir: str
    tools_policy: str = "read-only"
    resume_session_id: str = ""
    read_dirs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PhaseOutput:
    data: dict[str, Any]
    session_id: str
    result_path: str
    events_path: str
