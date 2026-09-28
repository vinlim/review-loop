"""opencode run behind the AgentAdapter protocol: JSON events on stdout, the answer as the reply's last JSON block.

opencode has no schema-constrained output, so the schema goes into the prompt and the coordinator validates what
comes back. Permissions are set on a tool-owned agent through OPENCODE_CONFIG_CONTENT, which outranks a reviewed
repository's own opencode config key by key. Config is merged, though, so the checkout's plugins, MCP servers and
formatters still load; the coordinator will not start opencode once a PR or fix changes them (startup_files)."""

from __future__ import annotations

import json
from pathlib import Path

from review_loop.adapters.agents.common import classify_failure, extract_json_object, validate_against, with_schema_instructions
from review_loop.types.agents import AgentError, AgentFailure, PhaseOutput, PhaseRequest
from review_loop.types.protocols import ProcessRunner
from review_loop.types.result import Err, Ok, Result

AGENT = "review-loop"
READ_ONLY_BASH = {"*": "deny", "git diff*": "allow", "git log*": "allow", "git show*": "allow", "git status*": "allow",
                  "git blame*": "allow", "ls*": "allow"}
WRITE_BASH = {"*": "allow", "gh *": "deny", "git push*": "deny", "git commit*": "deny"}


class OpencodeAdapter:
    def __init__(self, process: ProcessRunner):
        self.process = process

    def run(self, request: PhaseRequest) -> Result[PhaseOutput, AgentError]:
        output_dir = Path(request.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        events_path = output_dir / "events.jsonl"
        env = {**request.env, "OPENCODE_CONFIG_CONTENT": json.dumps(self.config(request))}
        completed = self.process.run(self.argv(request), cwd=request.cwd, env=env, timeout_seconds=request.timeout_seconds,
                                     stdout_path=str(events_path))
        (output_dir / "stderr.txt").write_text(completed.stderr)
        if completed.timed_out:
            return Err(AgentError(AgentFailure.TIMEOUT, f"opencode exceeded {request.timeout_seconds}s"))
        events = _events(completed.stdout)
        errors = "\n".join(json.dumps(event.get("error", event)) for event in events if event.get("type") == "error")
        if completed.exit_code != 0 or errors:
            text = f"{errors}\n{completed.stderr}".strip()
            return Err(AgentError(classify_failure(text), f"opencode exited {completed.exit_code}: {text[-500:]}"))
        reply = "\n".join(str(event.get("part", {}).get("text", "")) for event in events if event.get("type") == "text")
        (output_dir / "reply.md").write_text(reply)
        extracted = extract_json_object(reply)
        if not extracted.ok:
            return extracted
        validated = validate_against(request.schema_path, extracted.value)
        if not validated.ok:
            return validated
        session = next((str(event["sessionID"]) for event in events if event.get("sessionID")), "")
        return Ok(PhaseOutput(validated.value, session, str(output_dir / "reply.md"), str(events_path)))

    @staticmethod
    def argv(request: PhaseRequest) -> list[str]:
        argv = ["opencode", "run", "--format", "json", "--dir", request.cwd, "--agent", AGENT]
        if request.model:
            argv += ["--model", request.model]
        if request.effort:
            argv += ["--variant", request.effort]
        if request.resume_session_id:
            argv += ["--session", request.resume_session_id, "--fork"]
        return argv + [with_schema_instructions(request.prompt, request.schema_path)]

    @staticmethod
    def config(request: PhaseRequest) -> dict:
        write = request.tools_policy == "write"
        external = {"*": "deny", **{f"{directory}/*": "allow" for directory in request.read_dirs}}
        permission = {"edit": "allow" if write else "deny", "bash": WRITE_BASH if write else READ_ONLY_BASH,
                      "external_directory": external, "question": "deny", "doom_loop": "deny"}
        if not write:
            permission.update({"webfetch": "deny", "websearch": "deny", "task": "deny"})
        return {"agent": {AGENT: {"mode": "primary", "permission": permission}}}


def _events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
