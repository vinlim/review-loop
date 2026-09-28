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
    never appends to it, which is what makes resuming the developer's own session safe. `startup_files` are checkout
    paths the CLI loads and runs before its permission flags apply, with no flag to stop it; an entry ending in `/`
    covers a directory."""

    binary: str
    install_hint: str
    default_model: str = ""
    default_effort: str = ""
    forks_sessions: bool = False
    startup_files: tuple[str, ...] = ()

    def startup_roots(self) -> list[str]:
        """The top-level names that hold every startup file, as git pathspecs."""
        return sorted({entry.split("/")[0] for entry in self.startup_files})

    def runs_at_startup(self, path: str) -> bool:
        """A parent of a startup file counts too: git lists a symlinked `.agents` or `.opencode` as that one name."""
        for entry in self.startup_files:
            target = entry.rstrip("/")
            if path == target or target.startswith(path + "/") or (entry.endswith("/") and path.startswith(entry)):
                return True
        return False


AGENT_PROFILES = {
    "agy": AgentProfile("agy", "install the Antigravity CLI, then run `agy` once to sign in",
                        startup_files=(".agents/hooks.json", ".agents/mcp_config.json", ".agents/plugins/", ".agents/agents/")),
    "claude": AgentProfile("claude", "install Claude Code, then run `claude` once to log in", "claude-opus-5-5", "xhigh",
                           forks_sessions=True),
    "codex": AgentProfile("codex", "install with `npm i -g @openai/codex`, then run `codex login status`", "gpt-5.6-sol", "xhigh"),
    "opencode": AgentProfile("opencode", "install with `npm i -g opencode-ai`, then run `opencode auth login`", forks_sessions=True,
                             startup_files=("opencode.json", "opencode.jsonc", ".opencode/")),
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
