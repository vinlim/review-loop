import pytest

from review_loop.engine.findings import (
    FindingState, IllegalTransition, Severity, assign_ids, fingerprint, match_prior, transition,
)
from review_loop.types.findings import Finding


def finding(id="", title="Null branch skips the guard", file="app/X.php", line=10, symbol="guard", severity="ISSUE",
            state=FindingState.OPEN, supersedes="", fp=""):
    return Finding(id=id, pass_no=1, severity=Severity(severity), state=state, file=file, line=line, symbol=symbol,
                   title=title, finding="", protected_behaviour="", evidence="", recommendation="", proposed_refactor="",
                   fingerprint=fp or fingerprint(file, symbol, title), supersedes=supersedes, new_evidence="")


def test_ids_are_assigned_per_pass_in_order():
    ids = assign_ids(pass_no=3, count=3)

    assert ids == ["R3-F1", "R3-F2", "R3-F3"]


def test_a_fingerprint_survives_a_line_move_and_a_number_in_the_title():
    same = fingerprint("app/X.php", "guard", "Null branch skips the guard at line 10")
    moved = fingerprint("app/X.php", "guard", "Null branch skips the guard at line 42")
    other_file = fingerprint("app/Y.php", "guard", "Null branch skips the guard at line 10")

    assert same == moved and same != other_file


def test_supersedes_matches_a_prior_finding_by_id():
    prior = [finding(id="R1-F1", state=FindingState.REJECTED_PENDING_REVIEW)]

    matched, possible = match_prior(finding(title="Narrowed: the null branch", supersedes="R1-F1"), prior)

    assert (matched, possible) == ("R1-F1", "")


def test_a_fingerprint_match_without_supersedes_is_flagged_not_merged():
    prior = [finding(id="R1-F1", state=FindingState.REJECTION_ACCEPTED)]

    matched, possible = match_prior(finding(title="Null branch skips the guard"), prior)

    assert (matched, possible) == ("", "R1-F1")


def test_an_unknown_supersedes_id_is_ignored_and_reported_as_possible_duplicate_only_by_fingerprint():
    prior = [finding(id="R1-F1")]

    matched, possible = match_prior(finding(title="Different concern entirely", supersedes="R9-F9"), prior)

    assert (matched, possible) == ("", "")


@pytest.mark.parametrize("state, event, expected", [
    (FindingState.OPEN, "accept", FindingState.ACCEPTED),
    (FindingState.OPEN, "reject", FindingState.REJECTED_PENDING_REVIEW),
    (FindingState.OPEN, "needs_alignment", FindingState.NEEDS_ALIGNMENT),
    (FindingState.OPEN, "answer", FindingState.ANSWERED),
    (FindingState.OPEN, "withdraw", FindingState.WITHDRAWN),
    (FindingState.ACCEPTED, "fixed", FindingState.FIXED_PENDING_VERIFICATION),
    (FindingState.FIXED_PENDING_VERIFICATION, "verify", FindingState.VERIFIED),
    (FindingState.FIXED_PENDING_VERIFICATION, "dispute", FindingState.OPEN),
    (FindingState.REJECTED_PENDING_REVIEW, "accept_rejection", FindingState.REJECTION_ACCEPTED),
    (FindingState.REJECTED_PENDING_REVIEW, "dispute", FindingState.DISPUTED),
    (FindingState.DISPUTED, "align", FindingState.NEEDS_ALIGNMENT),
    (FindingState.NEEDS_ALIGNMENT, "decide_fix", FindingState.ACCEPTED),
    (FindingState.NEEDS_ALIGNMENT, "decide_keep", FindingState.REJECTION_ACCEPTED),
    (FindingState.NEEDS_ALIGNMENT, "except", FindingState.DEFERRED_BY_DECISION),
])
def test_the_lifecycle_allows_the_planned_transitions(state, event, expected):
    assert transition(state, event) == expected


@pytest.mark.parametrize("state, event", [
    (FindingState.VERIFIED, "accept"),
    (FindingState.OPEN, "verify"),
    (FindingState.REJECTION_ACCEPTED, "dispute"),
    (FindingState.ACCEPTED, "reject"),
])
def test_any_other_transition_is_a_programmer_error(state, event):
    with pytest.raises(IllegalTransition):
        transition(state, event)


def test_open_required_findings_are_blockers_and_issues_that_are_not_closed():
    from review_loop.engine.findings import is_open_required

    assert is_open_required(finding(severity="BLOCKER", state=FindingState.OPEN))
    assert is_open_required(finding(severity="ISSUE", state=FindingState.DISPUTED))
    assert not is_open_required(finding(severity="ISSUE", state=FindingState.VERIFIED))
    assert not is_open_required(finding(severity="CHORE", state=FindingState.OPEN))
    assert not is_open_required(finding(severity="ISSUE", state=FindingState.DEFERRED_BY_DECISION))
