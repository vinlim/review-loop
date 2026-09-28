"""agy -p (Antigravity CLI) behind the AgentAdapter protocol: schema-shaped output in a JSON envelope.

Headless agy allows file writes inside the workspace whatever the phase, so read-only rests on the coordinator's
worktree check; `--sandbox` restricts the terminal. Workspace hooks, MCP servers and plugins under .agents/ load
from the checkout with no flag to stop them; the coordinator will not start agy once a PR or fix changes them
(startup_files). It cannot fork a conversation, so it never resumes the developer's own."""

from __future__ import annotations

from pathlib import Path

from review_loop.adapters.agents.common import classify_failure, parse_json_text, validate_against
from review_loop.types.agents import AgentError, AgentFailure, PhaseOutput, PhaseRequest
from review_loop.types.protocols import ProcessRunner
from review_loop.types.result import Err, Ok, Result


class AgyAdapter:
    def __init__(self, process: ProcessRunner):
        self.process = process

    def run(self, request: PhaseRequest) -> Result[PhaseOutput, AgentError]:
        output_dir = Path(request.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "result.json"
        completed = self.process.run(self.argv(request), cwd=request.cwd, env=request.env, timeout_seconds=request.timeout_seconds)
        result_path.write_text(completed.stdout)
        (output_dir / "stderr.txt").write_text(completed.stderr)
        if completed.timed_out:
            return Err(AgentError(AgentFailure.TIMEOUT, f"agy exceeded {request.timeout_seconds}s"))
        envelope = _envelope(completed.stdout)
        if completed.exit_code == 0 and not envelope:
            return Err(AgentError(AgentFailure.MALFORMED_OUTPUT, "agy printed no JSON result"))
        if completed.exit_code != 0 or envelope.get("status") != "SUCCESS":
            text = f"{envelope.get('error', '')}\n{completed.stderr}".strip()
            return Err(AgentError(classify_failure(text), f"agy {envelope.get('status', f'exited {completed.exit_code}')}: {text[-500:]}"))
        if "structured_output" not in envelope:
            return Err(AgentError(AgentFailure.MALFORMED_OUTPUT, "agy result has no structured_output"))
        validated = validate_against(request.schema_path, envelope["structured_output"])
        if not validated.ok:
            return validated
        return Ok(PhaseOutput(validated.value, str(envelope.get("conversation_id", "")), str(result_path), str(result_path)))

    @staticmethod
    def argv(request: PhaseRequest) -> list[str]:
        argv = ["agy", "-p", request.prompt, "--output-format", "json", "--json-schema", request.schema_path,
                "--print-timeout", f"{request.timeout_seconds}s"]
        if request.model:
            argv += ["--model", request.model]
        if request.effort:
            argv += ["--effort", request.effort]
        if request.resume_session_id:
            argv += ["--conversation", request.resume_session_id]
        for directory in request.read_dirs:
            argv += ["--add-dir", directory]
        return argv + (["--dangerously-skip-permissions"] if request.tools_policy == "write" else ["--sandbox"])


def _envelope(stdout: str) -> dict:
    parsed = parse_json_text(stdout, "agy result") if stdout.strip() else None
    return parsed.value if parsed is not None and parsed.ok and isinstance(parsed.value, dict) else {}
