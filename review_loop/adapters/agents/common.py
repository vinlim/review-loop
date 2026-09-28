"""What both agent adapters share: schema validation of the output and the reading of failure text."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from review_loop.types.agents import AgentError, AgentFailure
from review_loop.types.result import Err, Ok, Result

_USAGE_LIMIT = re.compile(r"usage limit|rate limit|too many requests|\b429\b|quota", re.I)
_AUTH = re.compile(r"not logged in|please run /login|authentication|unauthori[sz]ed|\b401\b|login required|invalid api key", re.I)


def classify_failure(text: str, default: AgentFailure = AgentFailure.PROCESS_FAILED) -> AgentFailure:
    if _USAGE_LIMIT.search(text):
        return AgentFailure.USAGE_LIMIT
    if _AUTH.search(text):
        return AgentFailure.AUTH_REQUIRED
    return default


def validate_against(schema_path: str, data: object) -> Result[dict, AgentError]:
    schema = json.loads(Path(schema_path).read_text())
    if not isinstance(data, dict):
        return Err(AgentError(AgentFailure.MALFORMED_OUTPUT, f"structured output is {type(data).__name__}, expected an object"))
    errors = sorted(jsonschema.Draft7Validator(schema).iter_errors(data), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = "$" + "".join(f".{part}" if isinstance(part, str) else f"[{part}]" for part in first.absolute_path)
        return Err(AgentError(AgentFailure.SCHEMA_VIOLATION, f"{location}: {first.message}"))
    return Ok(data)


def parse_json_text(text: str, what: str) -> Result[object, AgentError]:
    try:
        return Ok(json.loads(text))
    except json.JSONDecodeError as error:
        return Err(AgentError(AgentFailure.MALFORMED_OUTPUT, f"{what} is not JSON: {error}"))
