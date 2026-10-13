import pytest

from review_loop.engine.prompts import fill_template


def test_a_partial_fill_leaves_the_placeholders_it_has_no_value_for():
    text = "{{shared}} then {{pr_number}}"

    partly = fill_template(text, {"shared": "block"}, partial=True)

    assert partly == "block then {{pr_number}}"
    assert fill_template(partly, {"pr_number": "7"}) == "block then 7"


def test_a_full_fill_still_refuses_a_placeholder_with_no_value():
    with pytest.raises(KeyError):
        fill_template("{{missing}}", {})
