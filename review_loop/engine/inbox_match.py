"""Whether a new adjacent finding is one already in the inbox: clear on title and file, suggested on file alone."""

from __future__ import annotations

import re


def match_inbox_item(new_item: dict, existing: list[dict]) -> tuple[int | None, list[int]]:
    title = _normalise(new_item.get("title", ""))
    file = new_item.get("file", "")
    ambiguous = []
    for item in existing:
        if _normalise(item.get("title", "")) == title and item.get("file", "") == file:
            return item["id"], []
        if file and item.get("file", "") == file:
            ambiguous.append(item["id"])
    return None, ambiguous


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", "", text.lower()).split())
