"""No attribution trailers or provenance lines reach a commit; the coordinator strips and records them."""

from __future__ import annotations


def forbidden_trailer_lines(message: str, forbidden: list[str]) -> list[str]:
    needles = [token.lower() for token in forbidden]
    return [line for line in message.splitlines() if any(needle in line.lower() for needle in needles)]


def strip_forbidden_trailers(message: str, forbidden: list[str]) -> str:
    offending = set(forbidden_trailer_lines(message, forbidden))
    kept = [line for line in message.splitlines() if line not in offending]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + ("\n" if message.endswith("\n") else "")


def clean_for_github(body: str, forbidden: list[str]) -> str:
    """Anything bound for GitHub loses provenance lines the same way a commit message does."""
    return strip_forbidden_trailers(body, forbidden)
