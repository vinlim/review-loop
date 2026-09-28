import json
from pathlib import Path

from review_loop.adapters.agents.claude import ClaudeAdapter
from review_loop.types.agents import AgentFailure, PhaseRequest
from tests.fakes.process import FakeProcessRunner

SCHEMAS = Path(__file__).resolve().parents[2] / "review_loop" / "schemas"
SAMPLE = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "agent_outputs" / "claude_result_sample.json").read_text())

VALID_ASSESSMENT = {"dispositions": [], "adjacent_findings": [], "summary": "nothing to do"}


def result_json(structured=VALID_ASSESSMENT, subtype="success", is_error=False, result="done"):
    return json.dumps({**SAMPLE, "subtype": subtype, "is_error": is_error, "result": result,
                       "structured_output": structured, "session_id": "sess-1"})


def request(tmp_path, phase="assess", tools_policy="read-only", resume=""):
    return PhaseRequest(phase=phase, prompt="assess this", schema_path=str(SCHEMAS / "assessment.json"), cwd="/wt",
                        env={"PATH": "/usr/bin"}, timeout_seconds=1200, model="claude-fable-5-1", effort="high",
                        output_dir=str(tmp_path), tools_policy=tools_policy, resume_session_id=resume)


def test_the_read_only_argv_restricts_tools_and_denies_edits_and_mcp(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json())

    result = ClaudeAdapter(process).run(request(tmp_path))

    assert result.ok and result.value.data == VALID_ASSESSMENT and result.value.session_id == "sess-1"
    call = process.calls[0]
    argv = call["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1])["title"] == "assessment"
    assert argv[argv.index("--model") + 1] == "claude-fable-5-1" and argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--permission-mode") + 1] == "default"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--strict-mcp-config" in argv and argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob,Bash"
    disallowed = argv[argv.index("--disallowedTools") + 1:]
    assert {"Edit", "Write", "NotebookEdit", "Bash(git commit:*)", "Bash(git push:*)", "Bash(gh:*)"} <= set(disallowed)
    allowed = argv[argv.index("--allowedTools") + 1: argv.index("--disallowedTools")]
    assert "Bash(git diff:*)" in allowed and "Bash(git log:*)" in allowed
    assert call["stdin"] == "assess this" and call["cwd"] == "/wt"
    assert (tmp_path / "result.json").exists()


def test_the_write_argv_bypasses_prompts_but_still_denies_gh_push_and_commit(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json())

    ClaudeAdapter(process).run(request(tmp_path, phase="fix", tools_policy="write"))

    argv = process.calls[0]["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--tools" not in argv
    disallowed = argv[argv.index("--disallowedTools") + 1:]
    assert {"Bash(gh:*)", "Bash(git push:*)", "Bash(git commit:*)"} <= set(disallowed)
    assert "Edit" not in disallowed


def test_a_resumed_session_reapplies_the_same_restrictions(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json())

    ClaudeAdapter(process).run(request(tmp_path, resume="sess-0"))

    argv = process.calls[0]["argv"]
    assert argv[argv.index("--resume") + 1] == "sess-0"
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob,Bash"


def test_a_success_without_structured_output_is_malformed(tmp_path):
    process = FakeProcessRunner()
    body = json.loads(result_json())
    del body["structured_output"]
    process.script(["claude", "-p"], stdout=json.dumps(body))

    result = ClaudeAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.MALFORMED_OUTPUT


def test_an_error_subtype_about_usage_limits_yields_usage_limit(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], exit_code=1, stdout=result_json(subtype="error_during_execution", is_error=True,
                                                                      result="You've hit your usage limit; resets at 6pm"))

    result = ClaudeAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.USAGE_LIMIT


def test_an_authentication_failure_yields_auth_required(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], exit_code=1, stdout=result_json(subtype="error_during_execution", is_error=True,
                                                                      result="Not logged in. Please run /login"))

    result = ClaudeAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.AUTH_REQUIRED


def test_output_that_violates_the_schema_is_reported_with_its_path(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json(structured={"dispositions": [], "summary": "x"}))

    result = ClaudeAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.SCHEMA_VIOLATION
    assert "adjacent_findings" in result.error.detail


def test_the_pass_directory_is_granted_with_add_dir_so_the_packet_is_readable(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json())
    req = request(tmp_path)
    req = PhaseRequest(**{**req.__dict__, "read_dirs": [str(tmp_path)]})

    ClaudeAdapter(process).run(req)

    argv = process.calls[0]["argv"]
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path)


def test_a_non_zero_exit_is_a_failure_even_with_a_success_envelope(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], exit_code=1, stdout=result_json())

    result = ClaudeAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.PROCESS_FAILED


def test_a_resumed_session_is_forked_so_the_developers_own_session_is_never_appended_to(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json())

    ClaudeAdapter(process).run(request(tmp_path, resume="sess-0"))

    argv = process.calls[0]["argv"]
    assert argv[argv.index("--resume") + 1] == "sess-0" and "--fork-session" in argv


def test_attribution_is_switched_off_through_settings_and_the_shell_readers_are_gone_from_the_read_only_list(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json())

    ClaudeAdapter(process).run(request(tmp_path))

    argv = process.calls[0]["argv"]
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["includeCoAuthoredBy"] is False and settings["attribution"] == {"commit": "", "pr": ""}
    allowed = argv[argv.index("--allowedTools") + 1: argv.index("--disallowedTools")]
    assert not any(tool.startswith(prefix) for tool in allowed for prefix in ("Bash(find", "Bash(sed", "Bash(rg", "Bash(cat", "Bash(head", "Bash(tail", "Bash(wc"))
    assert "Bash(git diff:*)" in allowed


def test_the_adapter_adds_its_own_login_to_the_process_environment(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json())

    ClaudeAdapter(process, oauth_token="author-token").run(request(tmp_path))

    env = process.calls[0]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "author-token" and env["PATH"] == "/usr/bin"


def test_without_a_login_the_adapter_passes_the_request_environment_unchanged(tmp_path):
    process = FakeProcessRunner()
    process.script(["claude", "-p"], stdout=result_json())

    ClaudeAdapter(process).run(request(tmp_path))

    assert process.calls[0]["env"] == {"PATH": "/usr/bin"}
