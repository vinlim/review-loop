"""Before a run is driven, each agent answers one structured probe with the model and effort a phase would give it, through
the same adapter and environment, so a CLI that cannot serve its model is found before any phase starts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from review_loop.config.settings import RepositoryConfig
from review_loop.engine.env import sanitize_env
from review_loop.services.workspace import ensure_shims
from review_loop.types.agents import PhaseRequest
from review_loop.types.protocols import ProcessRunner
from review_loop.types.run import AgentChoice

PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
PROBE_PROMPT = ("Readiness probe from the review-loop coordinator. Do not read files, run commands or use tools. "
                "Return the structured output {\"ok\": true} and nothing else.")
PROBE_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class AgentProbe:
    role: str
    agent: str
    model: str
    effort: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Probing:
    """What a probe needs from the composition root: the adapters, the environment phases start from, and where to write."""

    agents: dict[str, Any]
    base_env: dict[str, str]
    output_dir: Path


def probe_agents(agents: dict[str, Any], repo: RepositoryConfig, *, state_dir: Path, base_env: dict[str, str], output_dir: Path,
                 process: ProcessRunner, timeout_seconds: int = PROBE_TIMEOUT_SECONDS, answers: dict | None = None) -> list[AgentProbe]:
    """One probe per role; roles that share an agent, model and effort share the answer. `answers` carries answers across
    calls so a doctor over several repositories asks each distinct choice once."""
    answers = answers if answers is not None else {}
    env = sanitize_env(base_env, repo.workspace.sanitize_env, str(ensure_shims(state_dir)))
    cwd = _probe_workspace(state_dir, process, env)
    probes = []
    for role, choice in repo.review.agents().items():
        key = (choice.agent, choice.model, choice.effort)
        if key not in answers:
            answers[key] = _ask(agents[choice.agent], choice, cwd, env, output_dir / role, timeout_seconds)
        probes.append(_probe(role, choice, answers[key]))
    return probes


def failures(probes: list[AgentProbe]) -> str:
    return "; ".join(probe.detail for probe in probes if not probe.ok)


def _probe_workspace(state_dir: Path, process: ProcessRunner, env: dict[str, str]) -> Path:
    """An empty git repository of the tool's own: a phase always runs inside one (the PR worktree), and Codex refuses
    to run anywhere else."""
    workspace = state_dir / "probe-workspace"
    if not (workspace / ".git").exists():
        workspace.mkdir(parents=True, exist_ok=True)
        process.run(["git", "init", "-q", str(workspace)], cwd=str(state_dir), env=env, timeout_seconds=60)
    return workspace


def _ask(adapter, choice: AgentChoice, cwd: Path, env: dict[str, str], output_dir: Path, timeout_seconds: int):
    output_dir.mkdir(parents=True, exist_ok=True)
    schema_path = output_dir / "probe-schema.json"
    schema_path.write_text(json.dumps(PROBE_SCHEMA))
    request = PhaseRequest(phase="preflight", prompt=PROBE_PROMPT, schema_path=str(schema_path), cwd=str(cwd), env=env,
                           timeout_seconds=timeout_seconds, model=choice.model, effort=choice.effort, output_dir=str(output_dir))
    return adapter.run(request)


def _probe(role: str, choice: AgentChoice, result) -> AgentProbe:
    """A schema-valid answer is the adapter working; only the answer asked for is the agent ready."""
    if result.ok and result.value.data.get("ok") is True:
        return AgentProbe(role, choice.agent, choice.model, choice.effort, True, f"{role} {choice.agent} answered with {choice.model} at {choice.effort}")
    detail = f'answered {json.dumps(result.value.data)} instead of {{"ok": true}}' if result.ok else result.error.detail
    return AgentProbe(role, choice.agent, choice.model, choice.effort, False, f"{role} {choice.agent} cannot serve {choice.model} at {choice.effort}: {detail}")
