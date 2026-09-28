from review_loop.engine.findings import FindingState, Severity, fingerprint
from review_loop.engine.signals import SignalInput, detect_signals
from review_loop.types.findings import Finding


def finding(id, pass_no, state, severity="ISSUE", supersedes="", possible_dup="", new_evidence="", file="app/X.php", title=None):
    return Finding(id=id, pass_no=pass_no, severity=Severity(severity), state=state, file=file, line=1, symbol="", title=title or id,
                   finding="", protected_behaviour="", evidence="", recommendation="", proposed_refactor="",
                   fingerprint=fingerprint(file, "", title or id), supersedes=supersedes, possible_duplicate_of=possible_dup, new_evidence=new_evidence)


def base(**overrides):
    values = dict(findings=[], events={}, open_required_by_pass={}, blob_hashes={}, drift_by_pass={}, current_pass=2)
    values.update(overrides)
    return SignalInput(**values)


def test_a_re_raise_of_a_rejected_finding_without_new_evidence_is_a_signal():
    prior = finding("R1-F1", 1, FindingState.REJECTION_ACCEPTED)
    again = finding("R2-F1", 2, FindingState.OPEN, supersedes="R1-F1")

    signals = detect_signals(base(findings=[prior, again]))

    assert [(s.kind, s.finding_ids) for s in signals] == [("re-raise", ["R2-F1"])]


def test_a_re_raise_with_new_evidence_is_not_a_signal():
    prior = finding("R1-F1", 1, FindingState.REJECTION_ACCEPTED)
    again = finding("R2-F1", 2, FindingState.OPEN, supersedes="R1-F1", new_evidence="a second @ in the password leaks")

    assert detect_signals(base(findings=[prior, again])) == []


def test_a_fingerprint_match_on_a_rejected_finding_counts_as_a_re_raise_too():
    prior = finding("R1-F1", 1, FindingState.REJECTED_PENDING_REVIEW, title="Null guard skipped")
    again = finding("R2-F1", 2, FindingState.OPEN, possible_dup="R1-F1", title="Null guard skipped")

    assert [s.kind for s in detect_signals(base(findings=[prior, again]))] == ["re-raise"]


def test_a_reviewer_reversal_without_new_evidence_is_a_signal():
    f = finding("R1-F1", 1, FindingState.OPEN)
    events = {"R1-F1": [{"actor": "reviewer", "to_state": "verified", "note": {"resolution": "verified", "note": "ok"}},
                        {"actor": "reviewer", "to_state": "open", "note": {"resolution": "disputed", "note": ""}}]}

    assert [s.kind for s in detect_signals(base(findings=[f], events=events))] == ["reversal"]


def test_an_approve_followed_by_request_changes_on_new_commits_is_not_a_reversal():
    f = finding("R2-F1", 2, FindingState.OPEN)
    events = {"R2-F1": [{"actor": "reviewer", "to_state": "open", "note": {"pass": 2}}]}

    assert detect_signals(base(findings=[f], events=events)) == []


def test_a_file_that_flips_back_to_its_earlier_content_is_a_signal():
    hashes = {(1, "app/X.php"): "aaa", (2, "app/X.php"): "bbb", (3, "app/X.php"): "aaa"}

    signals = detect_signals(base(blob_hashes=hashes, current_pass=3))

    assert [(s.kind, s.detail) for s in signals] == [("flip-flop", "app/X.php")]


def test_no_convergence_when_open_required_counts_stop_falling_for_two_passes():
    signals = detect_signals(base(open_required_by_pass={1: 3, 2: 3, 3: 3}, current_pass=3))

    assert [s.kind for s in signals] == ["no-convergence"]
    assert detect_signals(base(open_required_by_pass={1: 3, 2: 2, 3: 2}, current_pass=3)) == []


def test_one_fingerprint_across_three_passes_is_no_convergence():
    same = [finding(f"R{n}-F1", n, FindingState.OPEN if n == 3 else FindingState.REJECTION_ACCEPTED, title="Guard") for n in (1, 2, 3)]

    assert "no-convergence" in [s.kind for s in detect_signals(base(findings=same, current_pass=3))]


def test_drift_two_passes_running_is_a_signal():
    assert [s.kind for s in detect_signals(base(drift_by_pass={1: ["a"], 2: ["b"]}, current_pass=2))] == ["drift"]
    assert detect_signals(base(drift_by_pass={1: ["a"], 2: []}, current_pass=2)) == []
