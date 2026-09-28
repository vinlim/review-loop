"""The envelope is shaped after the Antigravity CLI headless docs; no output from a real agy run is recorded yet."""

import json
from pathlib import Path

from review_loop.adapters.agents.agy import AgyAdapter
from review_loop.types.agents import AgentFailure, PhaseRequest
from tests.fakes.process import FakeProcessRunner

SCHEMAS = Path(__file__).resolve().parents[2] / "review_loop" / "schemas"
VALID_ASSESSMENT = {"dispositions": [], "adjacent_findings": [], "summary": "nothing to do"}


def envelope(structured=VALID_ASSESSMENT, status="SUCCESS", error=None):
    body = {"status": status, "response": json.dumps(structured), "conversation_id": "conv-1",
            "usage": {"input_tokens": 10, "output_tokens": 5}}
    if structured is not None:
        body["structured_output"] = structured
    if error:
        body["error"] = error
    return json.dumps(body)


def request(tmp_path, tools_policy="read-only", resume="", model="gemini-3.8-pro", effort="high", read_dirs=()):
    return PhaseRequest(phase="assess", prompt="assess this", schema_path=str(SCHEMAS / "assessment.json"), cwd="/wt",
                        env={"PATH": "/usr/bin"}, timeout_seconds=1200, model=model, effort=effort, output_dir=str(tmp_path),
                        tools_policy=tools_policy, resume_session_id=resume, read_dirs=list(read_dirs))


def run(tmp_path, stdout=None, **kwargs):
    process = FakeProcessRunner()
    process.script(["agy", "-p"], stdout=envelope() if stdout is None else stdout, **{k: kwargs.pop(k) for k in ("exit_code", "stderr", "timed_out") if k in kwargs})
    result = AgyAdapter(process).run(request(tmp_path, **kwargs))
    return result, process.calls[0]


def test_a_read_only_run_asks_for_json_against_the_schema_in_the_sandbox_with_the_phase_timeout(tmp_path):
    result, call = run(tmp_path, read_dirs=["/runs/pass-1"])

    assert result.ok and result.value.data == VALID_ASSESSMENT and result.value.session_id == "conv-1"
    argv = call["argv"]
    assert argv[:3] == ["agy", "-p", "assess this"] and call["cwd"] == "/wt"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--json-schema") + 1] == str(SCHEMAS / "assessment.json")
    assert argv[argv.index("--print-timeout") + 1] == "1200s"
    assert argv[argv.index("--model") + 1] == "gemini-3.8-pro" and argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--add-dir") + 1] == "/runs/pass-1"
    assert "--sandbox" in argv and "--dangerously-skip-permissions" not in argv


def test_an_empty_model_and_effort_leave_the_choice_to_agy(tmp_path):
    _, call = run(tmp_path, model="", effort="")

    assert "--model" not in call["argv"] and "--effort" not in call["argv"]


def test_the_write_phase_skips_permission_prompts_outside_the_sandbox(tmp_path):
    _, call = run(tmp_path, tools_policy="write")

    assert "--dangerously-skip-permissions" in call["argv"] and "--sandbox" not in call["argv"]


def test_a_resumed_session_opens_that_conversation(tmp_path):
    _, call = run(tmp_path, resume="conv-0")

    assert call["argv"][call["argv"].index("--conversation") + 1] == "conv-0"


def test_an_error_status_about_quota_yields_usage_limit(tmp_path):
    result, _ = run(tmp_path, stdout=envelope(structured=None, status="ERROR", error="Quota exceeded for this model"), exit_code=1)

    assert not result.ok and result.error.kind == AgentFailure.USAGE_LIMIT


def test_an_error_status_about_login_yields_auth_required(tmp_path):
    result, _ = run(tmp_path, stdout=envelope(structured=None, status="ERROR", error="Authentication required: run agy to sign in"), exit_code=1)

    assert not result.ok and result.error.kind == AgentFailure.AUTH_REQUIRED


def test_a_success_without_structured_output_is_malformed(tmp_path):
    result, _ = run(tmp_path, stdout=envelope(structured=None))

    assert not result.ok and result.error.kind == AgentFailure.MALFORMED_OUTPUT


def test_output_that_violates_the_schema_is_reported_with_its_path(tmp_path):
    result, _ = run(tmp_path, stdout=envelope(structured={"dispositions": [], "summary": "x"}))

    assert not result.ok and result.error.kind == AgentFailure.SCHEMA_VIOLATION and "adjacent_findings" in result.error.detail


def test_a_timeout_is_reported_as_a_timeout(tmp_path):
    result, _ = run(tmp_path, stdout="", timed_out=True, exit_code=-9)

    assert not result.ok and result.error.kind == AgentFailure.TIMEOUT


def test_a_crash_without_an_envelope_is_classified_from_stderr(tmp_path):
    result, _ = run(tmp_path, stdout="", exit_code=1, stderr="error: not logged in")

    assert not result.ok and result.error.kind == AgentFailure.AUTH_REQUIRED
