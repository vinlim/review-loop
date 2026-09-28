import json
from pathlib import Path

from review_loop.adapters.agents.common import extract_json_object, with_schema_instructions
from review_loop.types.agents import AgentFailure

SCHEMA = Path(__file__).resolve().parents[2] / "review_loop" / "schemas" / "assessment.json"


def test_the_last_fenced_json_block_wins_over_earlier_ones():
    text = 'Draft:\n```json\n{"summary": "draft"}\n```\nFinal:\n```json\n{"summary": "final"}\n```\nDone.'

    assert extract_json_object(text).value == {"summary": "final"}


def test_a_bare_object_after_prose_is_found_without_a_fence():
    text = 'I read the packet. Here is the result: {"summary": "ok", "items": [{"a": "}"}]} Thanks.'

    assert extract_json_object(text).value == {"summary": "ok", "items": [{"a": "}"}]}


def test_text_with_no_json_object_is_malformed_output():
    result = extract_json_object("I could not finish the assessment.")

    assert not result.ok and result.error.kind == AgentFailure.MALFORMED_OUTPUT


def test_schema_instructions_carry_the_whole_schema_after_the_prompt():
    prompt = with_schema_instructions("Assess the findings.", str(SCHEMA))

    assert prompt.startswith("Assess the findings.")
    assert json.dumps(json.loads(SCHEMA.read_text()), indent=2) in prompt
    assert "only" in prompt.split("Assess the findings.")[1].lower()
