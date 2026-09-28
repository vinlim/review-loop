"""codex exec behind the AgentAdapter protocol: prompt on stdin, schema-shaped output in a file."""

from __future__ import annotations

import json
from pathlib import Path

from review_loop.adapters.agents.common import classify_failure, parse_json_text, validate_against
from review_loop.types.agents import AgentError, AgentFailure, PhaseOutput, PhaseRequest
from review_loop.types.protocols import ProcessRunner
from review_loop.types.result import Err, Ok, Result


class CodexAdapter:
    def __init__(self, process: ProcessRunner):
        self.process = process

    def run(self, request: PhaseRequest) -> Result[PhaseOutput, AgentError]:
        output_dir = Path(request.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "output.json"
        events_path = output_dir / "events.jsonl"
        argv = self.argv(request, result_path)
        result_path.unlink(missing_ok=True)
        completed = self.process.run(argv, cwd=request.cwd, env=request.env, timeout_seconds=request.timeout_seconds, stdin=request.prompt,
                                     stdout_path=str(events_path))
        (output_dir / "stderr.txt").write_text(completed.stderr)
        if completed.timed_out:
            return Err(AgentError(AgentFailure.TIMEOUT, f"codex exceeded {request.timeout_seconds}s"))
        if completed.exit_code != 0:
            kind = classify_failure(_error_text(completed.stdout) + "\n" + completed.stderr)
            return Err(AgentError(kind, f"codex exited {completed.exit_code}: {_last_lines(completed.stderr or completed.stdout)}"))
        if not result_path.exists():
            return Err(AgentError(AgentFailure.MALFORMED_OUTPUT, f"codex wrote no output file at {result_path}"))
        parsed = parse_json_text(result_path.read_text(), "codex output file")
        if not parsed.ok:
            return parsed
        validated = validate_against(request.schema_path, parsed.value)
        if not validated.ok:
            return validated
        return Ok(PhaseOutput(validated.value, _thread_id(completed.stdout), str(result_path), str(events_path)))

    @staticmethod
    def argv(request: PhaseRequest, result_path: Path) -> list[str]:
        sandbox = "workspace-write" if request.tools_policy == "write" else "read-only"
        argv = ["codex", "exec", "-C", request.cwd, "--sandbox", sandbox, "--ignore-user-config",
                "-m", request.model, "-c", f'model_reasoning_effort="{request.effort}"',
                "--output-schema", request.schema_path, "-o", str(result_path), "--json"]
        if request.resume_session_id:
            argv = ["codex", "exec", "resume", request.resume_session_id, *argv[2:]]
        return argv + ["-"]


def _thread_id(events: str) -> str:
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            return str(event.get("thread_id", ""))
    return ""


def _error_text(events: str) -> str:
    """Only error events count: an agent message about a PR on authentication is not an auth failure."""
    messages = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "error":
            messages.append(str(event.get("message", "")))
    return "\n".join(messages)


def _last_lines(text: str, count: int = 5) -> str:
    return "\n".join(text.strip().splitlines()[-count:])
