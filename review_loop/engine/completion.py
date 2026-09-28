"""What happens after a review pass: assess, align, complete, or stop on the budget."""

from __future__ import annotations

from dataclasses import dataclass

from review_loop.engine.findings import FindingState, Severity, is_open_required
from review_loop.types.findings import Finding
from review_loop.types.run import Outcome


@dataclass(frozen=True)
class Decision:
    next: str
    outcome: Outcome | None = None


def decide_after_review(findings: list[Finding], pass_no: int, max_passes: int, head_verified: bool = True) -> Decision:
    disputed = any(f.state == FindingState.DISPUTED for f in findings)
    if _needs_assessment(findings) or _needs_more_passes(findings) or disputed:
        if pass_no >= max_passes:
            return Decision("budget_exhausted", _exhausted_outcome(findings))
        return Decision("align" if disputed and not _needs_assessment(findings) else "assess")
    if not head_verified:
        return Decision("verify")
    return Decision("complete", _closed_outcome(findings))


def _needs_assessment(findings: list[Finding]) -> bool:
    for finding in findings:
        if finding.state != FindingState.OPEN:
            continue
        if finding.severity in (Severity.BLOCKER, Severity.ISSUE):
            return True
        if finding.severity == Severity.QUESTION and finding.blocking:
            return True
    return False


def _needs_more_passes(findings: list[Finding]) -> bool:
    pending = {FindingState.ACCEPTED, FindingState.FIXED_PENDING_VERIFICATION, FindingState.REJECTED_PENDING_REVIEW, FindingState.NEEDS_ALIGNMENT}
    return any(is_open_required(f) and FindingState(f.state) in pending for f in findings)


def _exhausted_outcome(findings: list[Finding]) -> Outcome:
    if any(is_open_required(f) and f.severity == Severity.BLOCKER for f in findings):
        return Outcome.BLOCKED
    return Outcome.COMPLETE_WITH_EXCEPTIONS


def _closed_outcome(findings: list[Finding]) -> Outcome:
    if any(f.state == FindingState.DEFERRED_BY_DECISION for f in findings):
        return Outcome.COMPLETE_WITH_EXCEPTIONS
    return Outcome.COMPLETE
