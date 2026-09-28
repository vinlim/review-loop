import json
from pathlib import Path

from review_loop.engine.discussion import (
    Role, classify_role, digest, make_marker, parse_marker, render_discussion,
)
from review_loop.types.discussion import Discussion, InlineComment, IssueComment, Review, ReviewThread

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "prs" / "984"


def review(id, body, author="vinlim", at="2026-09-24T00:03:45Z", state="COMMENTED", commit="9272073e"):
    return Review(id=id, author=author, body=body, state=state, commit_id=commit, submitted_at=at)


def inline(id, body, path="a.php", line=57, reply_to=None, author="vinlim", at="2026-09-24T00:03:45Z", review_id=1):
    return InlineComment(id=id, author=author, body=body, created_at=at, path=path, line=line, original_line=line,
                         side="RIGHT", in_reply_to_id=reply_to, review_id=review_id, commit_id="9272073e", diff_hunk="")


def issue(id, body, author="vinlim", at="2026-09-24T00:40:08Z"):
    return IssueComment(id=id, author=author, body=body, created_at=at)


def test_a_marker_round_trips_role_run_pass_kind_and_finding():
    marker = make_marker(Role.REVIEWER, run_id="webapp-984-1", pass_no=2, kind="review", finding_id="R2-F1")

    parsed = parse_marker("EXECUTIVE SUMMARY\n" + marker + "\nmore")

    assert parsed.role == Role.REVIEWER
    assert (parsed.run_id, parsed.pass_no, parsed.kind, parsed.finding_id) == ("webapp-984-1", 2, "review", "R2-F1")


def test_a_marked_post_takes_the_role_of_its_marker_whoever_posted_it():
    body = "text\n" + make_marker(Role.AUTHOR, "r", 1, "reply")

    assert classify_role(body, author="vinlim", pr_author="vinlim") == Role.AUTHOR


def test_an_unmarked_post_from_before_the_tool_is_classified_by_its_shape():
    assert classify_role("EXECUTIVE SUMMARY\n\nVerdict: APPROVE", "vinlim", "vinlim") == Role.REVIEWER
    assert classify_role("## Review response, round 2 (head abc)\n", "vinlim", "vinlim") == Role.AUTHOR
    assert classify_role("[BLOCKER] A plain object is logged unchanged", "vinlim", "vinlim") == Role.REVIEWER
    assert classify_role("File: app/X.php\nSeverity: [ISSUE]\nLocation: Line 7", "vinlim", "vinlim") == Role.REVIEWER


def test_an_unmarked_free_form_post_is_human():
    assert classify_role("Please hold this until the migration lands.", "vinlim", "vinlim") == Role.HUMAN


def test_rendering_groups_inline_threads_and_tags_every_entry_with_its_role():
    discussion = Discussion(
        reviews=[review(1, "EXECUTIVE SUMMARY\n\nVerdict: REQUEST CHANGES")],
        inline=[inline(10, "[BLOCKER] Unchecked null"), inline(11, "Accepted, fixed in abc.", reply_to=10, at="2026-09-24T00:39:54Z"),
                inline(12, "Can we also rename this?", path="b.php", line=3, at="2026-09-24T01:00:00Z")],
        issue_comments=[issue(20, "## Review response (head abc)\n\nBoth fixed.")],
        threads=[ReviewThread("PRRT_1", False, False, "a.php", 57, [10, 11]), ReviewThread("PRRT_2", True, False, "b.php", 3, [12])],
    )

    text = render_discussion(discussion, pr_author="vinlim")

    assert text.index("[reviewer] review #1") < text.index("[reviewer] inline #10")
    assert text.index("[reviewer] inline #10") < text.index("[author] reply #11")
    header = next(line for line in text.splitlines() if "inline #12" in line)
    assert header.startswith("### [human] inline #12") and "resolved" in header
    assert "[author] comment #20" in text
    assert "a.php:57" in text and "b.php:3" in text


def test_the_digest_changes_when_a_reply_is_added_and_stays_when_fetch_order_changes():
    base = Discussion(reviews=[review(1, "A")], inline=[inline(10, "x"), inline(12, "y", path="b.php")], issue_comments=[], threads=[])
    reordered = Discussion(reviews=[review(1, "A")], inline=[inline(12, "y", path="b.php"), inline(10, "x")], issue_comments=[], threads=[])
    with_reply = Discussion(reviews=[review(1, "A")], inline=[inline(10, "x"), inline(11, "r", reply_to=10), inline(12, "y", path="b.php")],
                            issue_comments=[], threads=[])

    assert digest(base) == digest(reordered)
    assert digest(base) != digest(with_reply)


def test_the_digest_changes_when_a_thread_is_resolved():
    open_thread = Discussion([], [inline(10, "x")], [], [ReviewThread("PRRT_1", False, False, "a.php", 57, [10])])
    resolved = Discussion([], [inline(10, "x")], [], [ReviewThread("PRRT_1", True, False, "a.php", 57, [10])])

    assert digest(open_thread) != digest(resolved)


def test_the_recorded_pr_984_thread_renders_every_surface():
    reviews = [Review(r["id"], r["user"]["login"], r["body"], r["state"], r["commit_id"], r["submitted_at"])
               for r in json.loads((FIXTURES / "reviews.json").read_text())]
    inline_comments = [InlineComment(c["id"], c["user"]["login"], c["body"], c["created_at"], c["path"], c["line"], c["original_line"],
                                     c["side"], c.get("in_reply_to_id"), c["pull_request_review_id"], c["commit_id"], c["diff_hunk"])
                       for c in json.loads((FIXTURES / "inline.json").read_text())]
    issues = [IssueComment(c["id"], c["user"]["login"], c["body"], c["created_at"]) for c in json.loads((FIXTURES / "issue_comments.json").read_text())]

    text = render_discussion(Discussion(reviews, inline_comments, issues, []), pr_author="vinlim")

    assert text.count("[reviewer] review #") == 7
    assert text.count("[author] comment #") == 6
    assert text.count("[author] reply #") == 9
    assert "[human]" not in text
