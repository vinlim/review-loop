"""Finding identity and lifecycle: stable ids, fingerprints that survive line moves, and the allowed transitions."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum

from review_loop.types.findings import Finding


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    ISSUE = "ISSUE"
    CHORE = "CHORE"
    QUESTION = "QUESTION"


class FindingState(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    FIXED_PENDING_VERIFICATION = "fixed_pending_verification"
    VERIFIED = "verified"
    REJECTED_PENDING_REVIEW = "rejected_pending_review"
    REJECTION_ACCEPTED = "rejection_accepted"
    DISPUTED = "disputed"
    NEEDS_ALIGNMENT = "needs_alignment"
    DEFERRED_BY_DECISION = "deferred_by_decision"
    WITHDRAWN = "withdrawn"
    ANSWERED = "answered"


class IllegalTransition(Exception):
    pass


_TRANSITIONS: dict[tuple[FindingState, str], FindingState] = {
    (FindingState.OPEN, "accept"): FindingState.ACCEPTED,
    (FindingState.OPEN, "reject"): FindingState.REJECTED_PENDING_REVIEW,
    (FindingState.OPEN, "needs_alignment"): FindingState.NEEDS_ALIGNMENT,
    (FindingState.OPEN, "answer"): FindingState.ANSWERED,
    (FindingState.OPEN, "withdraw"): FindingState.WITHDRAWN,
    (FindingState.ACCEPTED, "fixed"): FindingState.FIXED_PENDING_VERIFICATION,
    (FindingState.ACCEPTED, "unfixed"): FindingState.REJECTED_PENDING_REVIEW,
    (FindingState.ACCEPTED, "withdraw"): FindingState.WITHDRAWN,
    (FindingState.FIXED_PENDING_VERIFICATION, "verify"): FindingState.VERIFIED,
    (FindingState.FIXED_PENDING_VERIFICATION, "dispute"): FindingState.OPEN,
    (FindingState.FIXED_PENDING_VERIFICATION, "withdraw"): FindingState.WITHDRAWN,
    (FindingState.REJECTED_PENDING_REVIEW, "accept_rejection"): FindingState.REJECTION_ACCEPTED,
    (FindingState.REJECTED_PENDING_REVIEW, "dispute"): FindingState.DISPUTED,
    (FindingState.REJECTED_PENDING_REVIEW, "withdraw"): FindingState.WITHDRAWN,
    (FindingState.DISPUTED, "align"): FindingState.NEEDS_ALIGNMENT,
    (FindingState.DISPUTED, "withdraw"): FindingState.WITHDRAWN,
    (FindingState.NEEDS_ALIGNMENT, "decide_fix"): FindingState.ACCEPTED,
    (FindingState.NEEDS_ALIGNMENT, "decide_keep"): FindingState.REJECTION_ACCEPTED,
    (FindingState.NEEDS_ALIGNMENT, "except"): FindingState.DEFERRED_BY_DECISION,
    (FindingState.NEEDS_ALIGNMENT, "withdraw"): FindingState.WITHDRAWN,
}

CLOSED_STATES = frozenset({FindingState.VERIFIED, FindingState.REJECTION_ACCEPTED, FindingState.WITHDRAWN,
                           FindingState.DEFERRED_BY_DECISION, FindingState.ANSWERED})
REQUIRED_SEVERITIES = frozenset({Severity.BLOCKER, Severity.ISSUE})


def transition(state: FindingState, event: str) -> FindingState:
    try:
        return _TRANSITIONS[(FindingState(state), event)]
    except KeyError:
        raise IllegalTransition(f"a finding in state {state} cannot take event {event!r}") from None


def assign_ids(pass_no: int, count: int) -> list[str]:
    return [f"R{pass_no}-F{index}" for index in range(1, count + 1)]


def fingerprint(file: str, symbol: str, title: str) -> str:
    normalised = re.sub(r"[^a-z ]", "", title.lower())
    normalised = " ".join(normalised.split())
    return hashlib.sha1(f"{file}|{symbol}|{normalised}".encode()).hexdigest()[:12]


def match_prior(finding: Finding, prior: list[Finding]) -> tuple[str, str]:
    """(supersedes, possible_duplicate_of): the reviewer's explicit link wins; a fingerprint match is only flagged."""
    by_id = {item.id: item for item in prior}
    if finding.supersedes and finding.supersedes in by_id:
        return finding.supersedes, ""
    for item in prior:
        if item.fingerprint == finding.fingerprint and item.id != finding.id:
            return "", item.id
    return "", ""


def is_open_required(finding: Finding) -> bool:
    return Severity(finding.severity) in REQUIRED_SEVERITIES and FindingState(finding.state) not in CLOSED_STATES


def is_closed(finding: Finding) -> bool:
    return FindingState(finding.state) in CLOSED_STATES
