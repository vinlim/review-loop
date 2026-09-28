from review_loop.engine.completion import decide_after_review
from review_loop.engine.findings import FindingState, Severity, fingerprint
from review_loop.types.findings import Finding


def f(id, severity, state, blocking=True):
    return Finding(id=id, pass_no=1, severity=Severity(severity), state=state, file="a", line=1, symbol="", title=id, finding="",
                   protected_behaviour="", evidence="", recommendation="", proposed_refactor="", fingerprint=fingerprint("a", "", id), blocking=blocking)


def test_open_findings_go_to_assessment_while_passes_remain():
    decision = decide_after_review([f("R2-F1", "ISSUE", FindingState.OPEN)], pass_no=2, max_passes=7)

    assert decision.next == "assess" and decision.outcome is None


def test_everything_closed_completes_cleanly():
    decision = decide_after_review([f("R1-F1", "ISSUE", FindingState.VERIFIED), f("R1-F2", "CHORE", FindingState.OPEN),
                                    f("R1-F3", "QUESTION", FindingState.ANSWERED)], pass_no=2, max_passes=7)

    assert decision.next == "complete" and decision.outcome.value == "complete"


def test_an_open_chore_or_non_blocking_question_does_not_hold_completion_but_an_open_blocking_question_does():
    closed = [f("R1-F1", "ISSUE", FindingState.VERIFIED), f("R1-F2", "QUESTION", FindingState.OPEN, blocking=False)]
    assert decide_after_review(closed, pass_no=2, max_passes=7).next == "complete"

    blocking = [f("R1-F1", "ISSUE", FindingState.VERIFIED), f("R1-F2", "QUESTION", FindingState.OPEN, blocking=True)]
    assert decide_after_review(blocking, pass_no=2, max_passes=7).next == "assess"


def test_a_deferred_exception_completes_with_exceptions():
    decision = decide_after_review([f("R1-F1", "ISSUE", FindingState.DEFERRED_BY_DECISION)], pass_no=3, max_passes=7)

    assert decision.next == "complete" and decision.outcome.value == "complete_with_exceptions"


def test_disputes_with_nothing_open_go_to_alignment():
    decision = decide_after_review([f("R1-F1", "ISSUE", FindingState.DISPUTED)], pass_no=2, max_passes=7)

    assert decision.next == "align"


def test_the_last_pass_with_open_required_work_stops_with_exceptions_or_blocked():
    issues = [f("R7-F1", "ISSUE", FindingState.OPEN)]
    decision = decide_after_review(issues, pass_no=7, max_passes=7)
    assert decision.next == "budget_exhausted" and decision.outcome.value == "complete_with_exceptions"

    blockers = [f("R7-F1", "BLOCKER", FindingState.OPEN)]
    decision = decide_after_review(blockers, pass_no=7, max_passes=7)
    assert decision.next == "budget_exhausted" and decision.outcome.value == "blocked"
