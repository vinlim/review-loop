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
