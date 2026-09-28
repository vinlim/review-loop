from review_loop.engine.assessment import validate_dispositions


def test_every_active_finding_needs_exactly_one_disposition():
    result = validate_dispositions(["R1-F1", "R1-F2"], [{"finding_id": "R1-F1", "disposition": "accept"}])

    assert not result.ok and "R1-F2" in result.error


def test_a_disposition_for_an_unknown_or_inactive_finding_is_rejected():
    result = validate_dispositions(["R1-F1"], [{"finding_id": "R1-F1", "disposition": "accept"}, {"finding_id": "R9-F9", "disposition": "reject"}])

    assert not result.ok and "R9-F9" in result.error


def test_a_duplicate_disposition_is_rejected():
    result = validate_dispositions(["R1-F1"], [{"finding_id": "R1-F1", "disposition": "accept"}, {"finding_id": "R1-F1", "disposition": "reject"}])

    assert not result.ok and "twice" in result.error


def test_a_complete_set_passes():
    assert validate_dispositions(["R1-F1"], [{"finding_id": "R1-F1", "disposition": "reject"}]).ok
