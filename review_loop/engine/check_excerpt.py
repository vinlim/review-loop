"""The lines of a verification log that say what failed, for `show`: the operator should not have to open the log to learn it."""

from __future__ import annotations

import re

FAILURE_LINE = re.compile(r"\b(fail(ed|ure|ures|s)?|errors?)\b", re.I)
BLOCK_LINES = 8
TAIL_LINES = 3


def failure_excerpt(blocks: list[tuple[str, str]]) -> list[str]:
    """`blocks` is each command's status and its log block (`$ argv`, `exit N`, then its output). Failed and unavailable
    blocks explain the result; a block that only selected nothing does so when nothing else went wrong. Each gives its
    command and exit, then the lines naming a failure, or its last lines when none does."""
    explaining = [text for status, text in blocks if status in ("failed", "unavailable")]
    if not explaining:
        explaining = [text for status, text in blocks if status == "nothing_selected"]
    excerpt: list[str] = []
    for text in explaining:
        lines = [line.strip() for line in text.splitlines()]
        header, body = lines[:2], [line for line in lines[2:] if line]
        named = [line for line in body if FAILURE_LINE.search(line)]
        excerpt += header + (named[:BLOCK_LINES] if named else body[-TAIL_LINES:])
    return excerpt
