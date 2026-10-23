from review_loop.engine.reply_render import render_summary

MARKER = "<!-- review-loop role=author run=r pass=2 kind=summary -->"


def test_a_pass_without_an_assessment_summary_says_what_the_developer_decided():
    rows = [{"id": "R1-F2", "disposition": "developer_fix", "commit": "abc123456"}, {"id": "R1-F3", "disposition": "developer_keep", "commit": ""}]

    body = render_summary(1004, 2, "abc123456" + "0" * 31, "", rows, "passed", [], 0, [], MARKER)

    assert "Fixed on a developer decision: R1-F2. Kept as is on a developer decision: R1-F3." in body
    assert "| R1-F2 | developer decision: fix | abc123456 |" in body and "| R1-F3 | developer decision: keep |  |" in body


def test_the_assessment_summary_stands_when_there_is_one():
    rows = [{"id": "R2-F1", "disposition": "accept", "commit": "abc123456"}, {"id": "R1-F2", "disposition": "developer_fix", "commit": "abc123456"}]

    body = render_summary(1004, 2, "abc123456" + "0" * 31, "one accepted", rows, "passed", [], 0, [], MARKER)

    assert "\n\none accepted\n\n" in body and "on a developer decision" not in body
