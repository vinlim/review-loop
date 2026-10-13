from review_loop.engine.review_render import render_finding_comment, render_review_body

MARKER = "<!-- review-loop role=reviewer run=r pass=1 kind=review -->"


def a_review(**overrides):
    review = {"verdict": "REQUEST_CHANGES", "risk": "MEDIUM", "summary": "Two gaps.",
              "contract": {"owns": "Read-only search of live conversations.", "does_not_own": "Historical backfills."},
              "findings": [], "resolved_prior": [], "questions": [], "verification": []}
    return {**review, **overrides}


def test_the_contract_is_two_lines_with_one_full_stop_each_whatever_the_reviewer_ended_with():
    body = render_review_body(a_review(), [], MARKER)

    assert "Owns: Read-only search of live conversations.\nDoes not own: Historical backfills.\n" in body
    assert ".." not in body


def test_a_contract_phrase_without_a_full_stop_gets_one():
    review = a_review(contract={"owns": "the search tools", "does_not_own": "the merger"})

    body = render_review_body(review, [], MARKER)

    assert "Owns: the search tools.\nDoes not own: the merger." in body


def test_a_finding_title_ending_in_a_full_stop_is_not_doubled():
    finding = {"file": "a.py", "severity": "ISSUE", "line": 3, "symbol": "", "title": "Bound the page.",
               "finding": "The first bucket is kept whole.", "protected_behaviour": "p", "evidence": "e", "recommendation": "r"}

    text = render_finding_comment("R1-F1", finding, "")

    assert "Finding: Bound the page. The first bucket is kept whole.\n" in text


def test_a_prior_resolution_reads_as_words():
    review = a_review(resolved_prior=[{"id": "R1-F4", "resolution": "rejection_accepted", "note": "The column holds seconds."}])

    body = render_review_body(review, [], MARKER)

    assert "Prior R1-F4: rejection accepted. The column holds seconds." in body
    assert "rejection_accepted" not in body
