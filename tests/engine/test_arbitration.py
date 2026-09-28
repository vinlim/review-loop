from review_loop.engine.arbitration import arbitration_values, combine_verdicts


def test_the_two_prompts_carry_swapped_labels_and_no_role_names():
    reviewer_position = "Roll back: the contract is all-or-nothing."
    author_position = "Complete the order; retry the notification separately."

    for_codex, for_claude = arbitration_values(reviewer_position, author_position)

    assert for_codex["position_a"] == reviewer_position and for_codex["position_b"] == author_position
    assert for_claude["position_a"] == author_position and for_claude["position_b"] == reviewer_position
    for values in (for_codex, for_claude):
        assert "reviewer" not in values["position_a"].lower() and "author" not in values["position_b"].lower()
        assert values["a_is"] in ("reviewer", "author") and values["b_is"] in ("reviewer", "author")


def test_matching_verdicts_decide():
    assert combine_verdicts("fix", "fix", "ISSUE").kind == "fix"
    assert combine_verdicts("keep", "keep", "BLOCKER").kind == "keep"


def test_a_split_on_an_issue_becomes_an_exception_and_on_a_blocker_blocks():
    assert combine_verdicts("fix", "keep", "ISSUE").kind == "exception"
    assert combine_verdicts("fix", "keep", "CHORE").kind == "exception"
    assert combine_verdicts("fix", "keep", "BLOCKER").kind == "blocked"


def test_two_neithers_fall_to_the_severity_rule():
    assert combine_verdicts("neither", "neither", "ISSUE").kind == "exception"
    assert combine_verdicts("neither", "neither", "BLOCKER").kind == "blocked"


def test_one_neither_and_one_choice_follows_the_choice():
    assert combine_verdicts("neither", "fix", "ISSUE").kind == "fix"
    assert combine_verdicts("keep", "neither", "ISSUE").kind == "keep"
