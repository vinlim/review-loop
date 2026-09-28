"""Blind arbitration: each arbiter sees the two positions under swapped labels; the verdicts combine by a fixed rule."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    kind: str
    reason: str


def arbitration_values(reviewer_position: str, author_position: str) -> tuple[dict[str, str], dict[str, str]]:
    """(values for the first arbiter, values for the second); the labels are swapped between them."""
    first = {"position_a": reviewer_position, "position_b": author_position, "a_is": "reviewer", "b_is": "author"}
    second = {"position_a": author_position, "position_b": reviewer_position, "a_is": "author", "b_is": "reviewer"}
    return first, second


def map_choice(choice: str, values: dict[str, str]) -> str:
    """A|B|neither under one labelling becomes fix (the reviewer's position) or keep (the author's)."""
    if choice == "neither":
        return "neither"
    holder = values["a_is"] if choice == "A" else values["b_is"]
    return "fix" if holder == "reviewer" else "keep"


def combine_verdicts(first: str, second: str, severity: str) -> Verdict:
    chosen = [choice for choice in (first, second) if choice != "neither"]
    if len(chosen) == 2 and chosen[0] == chosen[1]:
        return Verdict(chosen[0], "both arbiters agree")
    if len(chosen) == 1 and severity != "BLOCKER":
        return Verdict(chosen[0], "one arbiter decided, the other chose neither")
    if severity == "BLOCKER":
        return Verdict("blocked", f"split verdicts on a BLOCKER: {first} and {second}")
    return Verdict("exception", f"split verdicts on an {severity}: {first} and {second}")
