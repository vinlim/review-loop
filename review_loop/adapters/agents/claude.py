"""claude -p behind the AgentAdapter protocol, with the tool surface set per phase, never by prompt alone."""

from __future__ import annotations

import json
from pathlib import Path

from review_loop.adapters.agents.common import classify_failure, parse_json_text, validate_against
from review_loop.types.agents import AgentError, AgentFailure, PhaseOutput, PhaseRequest
from review_loop.types.protocols import ProcessRunner
from review_loop.types.result import Err, Ok, Result

READ_ONLY_TOOLS = "Read,Grep,Glob,Bash"
READ_ONLY_ALLOWED = ["Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)", "Bash(git status:*)", "Bash(git blame:*)",
                     "Bash(ls:*)", "Bash(php -l:*)"]
NO_ATTRIBUTION = '{"includeCoAuthoredBy": false, "attribution": {"commit": "", "pr": ""}}'
READ_ONLY_DENIED = ["Edit", "Write", "NotebookEdit", "Bash(git commit:*)", "Bash(git push:*)", "Bash(gh:*)"]
WRITE_DENIED = ["Bash(gh:*)", "Bash(git push:*)", "Bash(git commit:*)"]
NO_MCP = '{"mcpServers":{}}'
# -p never asks for workspace trust, so project and local settings would run the checkout's hooks, env and helper
# commands at startup. This also drops the project's CLAUDE.md and rules; the packet names the instruction files.
USER_SETTINGS_ONLY = "user"


class ClaudeAdapter:
    def __init__(self, process: ProcessRunner):
        self.process = process

    def run(self, request: PhaseRequest) -> Result[PhaseOutput, AgentError]:
        output_dir = Path(request.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "result.json"
        argv = self.argv(request)
        completed = self.process.run(argv, cwd=request.cwd, env=request.env, timeout_seconds=request.timeout_seconds, stdin=request.prompt)
        result_path.write_text(completed.stdout)
        (output_dir / "stderr.txt").write_text(completed.stderr)
        if completed.timed_out:
            return Err(AgentError(AgentFailure.TIMEOUT, f"claude exceeded {request.timeout_seconds}s"))
        if completed.exit_code != 0:
            kind = classify_failure(completed.stdout + "\n" + completed.stderr)
            return Err(AgentError(kind, f"claude exited {completed.exit_code}: {(completed.stderr or completed.stdout).strip()[-500:]}"))
        parsed = parse_json_text(completed.stdout, "claude result")
        if not parsed.ok:
            return parsed
        result = parsed.value if isinstance(parsed.value, dict) else {}
        if result.get("is_error") or result.get("subtype") != "success":
            text = str(result.get("result", "")) + "\n" + completed.stderr
            return Err(AgentError(classify_failure(text), f"claude {result.get('subtype', 'unknown')}: {text.strip()[:500]}"))
        if "structured_output" not in result:
            return Err(AgentError(AgentFailure.MALFORMED_OUTPUT, "claude result has no structured_output"))
        validated = validate_against(request.schema_path, result["structured_output"])
        if not validated.ok:
            return validated
        return Ok(PhaseOutput(validated.value, str(result.get("session_id", "")), str(result_path), str(result_path)))

    @staticmethod
    def argv(request: PhaseRequest) -> list[str]:
        schema_text = Path(request.schema_path).read_text()
        argv = ["claude", "-p", "--output-format", "json", "--json-schema", schema_text,
                "--model", request.model, "--effort", request.effort,
                "--permission-prompts", "none", "--strict-mcp-config", "--mcp-config", NO_MCP,
                "--setting-sources", USER_SETTINGS_ONLY, "--settings", NO_ATTRIBUTION]
        if request.resume_session_id:
            argv += ["--resume", request.resume_session_id, "--fork-session"]
        for directory in request.read_dirs:
            argv += ["--add-dir", directory]
        if request.tools_policy == "write":
            return argv + ["--permission-mode", "bypassPermissions", "--disallowedTools", *WRITE_DENIED]
        return argv + ["--permission-mode", "default", "--tools", READ_ONLY_TOOLS,
                       "--allowedTools", *READ_ONLY_ALLOWED, "--disallowedTools", *READ_ONLY_DENIED]
