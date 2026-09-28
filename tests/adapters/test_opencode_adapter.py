"""Events are shaped after `opencode run --format json` as documented by the community; no real run is recorded yet."""

import json
from pathlib import Path

from review_loop.adapters.agents.opencode import OpencodeAdapter
from review_loop.types.agents import AgentFailure, PhaseRequest
from tests.fakes.process import FakeProcessRunner

SCHEMAS = Path(__file__).resolve().parents[2] / "review_loop" / "schemas"
VALID_ASSESSMENT = {"dispositions": [], "adjacent_findings": [], "summary": "nothing to do"}


def events(*texts, session="ses_1", error=None):
    lines = [{"type": "step_start", "sessionID": session, "part": {"type": "step-start"}}]
    lines += [{"type": "text", "sessionID": session, "part": {"type": "text", "text": text}} for text in texts]
    if error:
        lines.append({"type": "error", "sessionID": session, "error": {"name": "APIError", "data": {"message": error}}})
    lines.append({"type": "step_finish", "sessionID": session, "part": {"type": "step-finish", "reason": "stop"}})
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def answer(data=VALID_ASSESSMENT):
    return f"I checked both findings.\n```json\n{json.dumps(data)}\n```"


def request(tmp_path, tools_policy="read-only", resume="", model="anthropic/claude-fable-5-1", effort="high", read_dirs=("/runs/pass-1",)):
    return PhaseRequest(phase="assess", prompt="assess this", schema_path=str(SCHEMAS / "assessment.json"), cwd="/wt",
                        env={"PATH": "/usr/bin"}, timeout_seconds=1200, model=model, effort=effort, output_dir=str(tmp_path),
                        tools_policy=tools_policy, resume_session_id=resume, read_dirs=list(read_dirs))


def run(tmp_path, stdout=None, exit_code=0, stderr="", timed_out=False, **kwargs):
    process = FakeProcessRunner()
    process.script(["opencode", "run"], stdout=events(answer()) if stdout is None else stdout, exit_code=exit_code, stderr=stderr, timed_out=timed_out)
    result = OpencodeAdapter(process).run(request(tmp_path, **kwargs))
    return result, process.calls[0]


def permissions(call):
    config = json.loads(call["env"]["OPENCODE_CONFIG_CONTENT"])
    return config["agent"]["review-loop"]["permission"]


def test_a_read_only_run_uses_the_tool_agent_with_the_schema_in_the_prompt_and_returns_the_last_json_block(tmp_path):
    result, call = run(tmp_path)

    assert result.ok and result.value.data == VALID_ASSESSMENT and result.value.session_id == "ses_1"
    argv = call["argv"]
    assert argv[:4] == ["opencode", "run", "--format", "json"] and call["cwd"] == "/wt"
    assert argv[argv.index("--dir") + 1] == "/wt" and argv[argv.index("--agent") + 1] == "review-loop"
    assert argv[argv.index("--model") + 1] == "anthropic/claude-fable-5-1" and argv[argv.index("--variant") + 1] == "high"
    assert argv[-1].startswith("assess this") and '"title": "assessment"' in argv[-1]
    assert call["env"]["PATH"] == "/usr/bin" and (tmp_path / "events.jsonl").exists()


def test_the_read_only_agent_denies_edits_and_all_but_read_only_git_and_can_read_the_pass_directory(tmp_path):
    _, call = run(tmp_path)

    rules = permissions(call)
    assert rules["edit"] == "deny" and rules["webfetch"] == "deny" and rules["question"] == "deny"
    assert rules["bash"]["*"] == "deny" and rules["bash"]["git diff*"] == "allow" and rules["bash"]["git log*"] == "allow"
    assert list(rules["external_directory"].items()) == [("*", "deny"), ("/runs/pass-1/*", "allow")]


def test_the_write_agent_may_edit_and_run_commands_but_not_gh_push_or_commit(tmp_path):
    _, call = run(tmp_path, tools_policy="write")

    rules = permissions(call)
    assert rules["edit"] == "allow" and rules["bash"]["*"] == "allow"
    assert rules["bash"]["gh *"] == "deny" and rules["bash"]["git push*"] == "deny" and rules["bash"]["git commit*"] == "deny"


def test_a_resumed_session_is_forked(tmp_path):
    _, call = run(tmp_path, resume="ses_0")

    argv = call["argv"]
    assert argv[argv.index("--session") + 1] == "ses_0" and "--fork" in argv


def test_an_empty_model_and_effort_leave_the_choice_to_opencode(tmp_path):
    _, call = run(tmp_path, model="", effort="")

    assert "--model" not in call["argv"] and "--variant" not in call["argv"]


def test_an_error_event_about_rate_limits_yields_usage_limit(tmp_path):
    result, _ = run(tmp_path, stdout=events(error="Rate limit exceeded, retry later"), exit_code=1)

    assert not result.ok and result.error.kind == AgentFailure.USAGE_LIMIT


def test_a_reply_without_a_json_object_is_malformed(tmp_path):
    result, _ = run(tmp_path, stdout=events("I could not finish."))

    assert not result.ok and result.error.kind == AgentFailure.MALFORMED_OUTPUT


def test_a_json_answer_that_violates_the_schema_is_reported_with_its_path(tmp_path):
    result, _ = run(tmp_path, stdout=events(answer({"dispositions": [], "summary": "x"})))

    assert not result.ok and result.error.kind == AgentFailure.SCHEMA_VIOLATION and "adjacent_findings" in result.error.detail


def test_a_timeout_is_reported_as_a_timeout(tmp_path):
    result, _ = run(tmp_path, stdout="", exit_code=-9, timed_out=True)

    assert not result.ok and result.error.kind == AgentFailure.TIMEOUT
