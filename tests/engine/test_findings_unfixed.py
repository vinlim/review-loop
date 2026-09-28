from review_loop.engine.findings import FindingState, transition


def test_an_accepted_finding_the_author_then_leaves_unchanged_becomes_a_pending_rejection():
    assert transition(FindingState.ACCEPTED, "unfixed") == FindingState.REJECTED_PENDING_REVIEW
