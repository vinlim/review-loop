import json
from pathlib import Path

from review_loop.adapters.agents.codex import CodexAdapter
from review_loop.types.agents import AgentFailure, PhaseRequest
from tests.fakes.process import FakeProcessRunner

SCHEMAS = Path(__file__).resolve().parents[2] / "review_loop" / "schemas"
EVENTS = '{"type":"thread.started","thread_id":"thr_123"}\n{"type":"turn.completed","usage":{}}\n'

VALID_REVIEW = {
    "verdict": "APPROVE", "risk": "LOW", "summary": "fine", "contract": {"owns": "x", "does_not_own": "y"},
    "findings": [], "resolved_prior": [], "questions": [], "verification": ["php -l ok"],
}


def request(tmp_path, phase="review", tools_policy="read-only"):
    return PhaseRequest(phase=phase, prompt="review this", schema_path=str(SCHEMAS / "review.json"), cwd="/wt",
                        env={"PATH": "/usr/bin"}, timeout_seconds=2400, model="gpt-6-astra", effort="ultra",
                        output_dir=str(tmp_path), tools_policy=tools_policy)


def test_the_codex_argv_carries_the_sandbox_the_model_the_schema_and_reads_the_prompt_from_stdin(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], stdout=EVENTS)
    process.on_run = lambda call: (tmp_path / "output.json").write_text(json.dumps(VALID_REVIEW))

    result = CodexAdapter(process).run(request(tmp_path))

    assert result.ok
    call = process.calls[0]
    argv = call["argv"]
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("-C") + 1] == "/wt"
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in argv
    assert argv[argv.index("-m") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="ultra"' in argv
    assert argv[argv.index("--output-schema") + 1] == str(SCHEMAS / "review.json")
    assert argv[argv.index("-o") + 1] == str(tmp_path / "output.json")
    assert "--json" in argv and argv[-1] == "-"
    assert call["stdin"] == "review this" and call["cwd"] == "/wt" and call["timeout"] == 2400
    assert result.value.data["verdict"] == "APPROVE"
    assert result.value.session_id == "thr_123"
    assert (tmp_path / "events.jsonl").read_text() == EVENTS


def test_a_non_zero_exit_yields_process_failed_with_the_stderr(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], exit_code=1, stderr="boom: sandbox denied")

    result = CodexAdapter(process).run(request(tmp_path))

    assert not result.ok
    assert result.error.kind == AgentFailure.PROCESS_FAILED and "sandbox denied" in result.error.detail


def test_a_missing_output_file_yields_malformed_output(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], stdout=EVENTS)

    result = CodexAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.MALFORMED_OUTPUT


def test_output_that_violates_the_schema_names_the_failing_path(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], stdout=EVENTS)
    process.on_run = lambda call: (tmp_path / "output.json").write_text(json.dumps({**VALID_REVIEW, "risk": "SEVERE"}))

    result = CodexAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.SCHEMA_VIOLATION
    assert "risk" in result.error.detail


def test_a_usage_limit_message_in_the_event_stream_yields_usage_limit(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], exit_code=1, stdout='{"type":"error","message":"You have hit your usage limit. Try again at 3pm."}\n')

    result = CodexAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.USAGE_LIMIT


def test_a_timeout_yields_timeout(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], exit_code=-1, timed_out=True)

    result = CodexAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.TIMEOUT


def test_a_write_phase_uses_the_workspace_write_sandbox(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], stdout=EVENTS)
    process.on_run = lambda call: (tmp_path / "output.json").write_text(json.dumps(VALID_REVIEW))

    CodexAdapter(process).run(request(tmp_path, tools_policy="write"))

    argv = process.calls[0]["argv"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"


def test_the_event_stream_is_written_to_the_events_file_as_it_arrives(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], stdout=EVENTS)
    process.on_run = lambda call: (tmp_path / "output.json").write_text(json.dumps(VALID_REVIEW))

    CodexAdapter(process).run(request(tmp_path))

    assert process.calls[0]["stdout_path"] == str(tmp_path / "events.jsonl")


def test_a_stale_output_file_from_an_earlier_attempt_is_never_accepted(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], stdout=EVENTS)
    stale = tmp_path / "output.json"
    stale.write_text(json.dumps(VALID_REVIEW))

    def run_without_writing(argv, cwd, env, timeout_seconds, stdin="", stdout_path=None):
        assert not stale.exists(), "the adapter must remove a stale output file before the run"
        return process.run(argv, cwd, env, timeout_seconds, stdin, stdout_path)

    class Runner:
        run = staticmethod(run_without_writing)

    result = CodexAdapter(Runner()).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.MALFORMED_OUTPUT


def test_an_agent_message_about_authentication_does_not_turn_a_crash_into_an_auth_pause(tmp_path):
    process = FakeProcessRunner()
    process.script(["codex", "exec"], exit_code=1,
                   stdout='{"type":"item.completed","item":{"type":"agent_message","text":"the PR changes authentication and login handling"}}\n',
                   stderr="segmentation fault")

    result = CodexAdapter(process).run(request(tmp_path))

    assert not result.ok and result.error.kind == AgentFailure.PROCESS_FAILED
