from review_loop.engine.trailers import forbidden_trailer_lines, strip_forbidden_trailers

FORBIDDEN = ["Co-Authored-By", "Generated with"]


def test_a_commit_message_with_a_forbidden_trailer_is_detected_and_stripped():
    message = "fix: close the guard\n\nBody line.\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"

    assert forbidden_trailer_lines(message, FORBIDDEN) == ["Co-Authored-By: Claude <noreply@anthropic.com>"]
    assert strip_forbidden_trailers(message, FORBIDDEN) == "fix: close the guard\n\nBody line.\n"


def test_provenance_prose_in_the_body_is_stripped_too():
    message = "fix: x\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"

    assert strip_forbidden_trailers(message, FORBIDDEN) == "fix: x\n"


def test_a_clean_message_is_returned_unchanged():
    message = "fix: x\n\nPlain body.\n"

    assert forbidden_trailer_lines(message, FORBIDDEN) == [] and strip_forbidden_trailers(message, FORBIDDEN) == message
