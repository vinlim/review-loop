"""Which lines of the diff can carry an inline comment: GitHub accepts only lines inside a hunk."""

from __future__ import annotations

import re

_FILE = re.compile(r"^\+\+\+ b/(.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def diff_lines(diff_text: str) -> dict[str, set[int]]:
    lines: dict[str, set[int]] = {}
    current = ""
    right = 0
    for raw in diff_text.splitlines():
        file_match = _FILE.match(raw)
        if file_match:
            current = file_match.group(1)
            lines.setdefault(current, set())
            continue
        hunk = _HUNK.match(raw)
        if hunk:
            right = int(hunk.group(1))
            continue
        if not current or raw.startswith("---") or raw.startswith("diff --git") or raw.startswith("\\"):
            continue
        if raw.startswith("+") or raw.startswith(" "):
            lines[current].add(right)
            right += 1
    return lines


def place(file: str, line: int, lines: dict[str, set[int]]) -> str:
    return "inline" if file and line in lines.get(file, set()) else "body"
