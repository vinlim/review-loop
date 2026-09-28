import json
from pathlib import Path

from review_loop.adapters.github_gh import GhGitHub
from review_loop.types.pull_request import PullRef
from tests.fakes.process import FakeProcessRunner

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "prs" / "984"
REF = PullRef("acme", "webapp", 984)


def load(name):
    return json.loads((FIXTURES / name).read_text())


def two_pages(items):
    half = len(items) // 2
    return json.dumps([items[:half], items[half:]])


def threads_page(nodes, has_next=False, cursor="C1"):
    return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}}}}})


def thread(node_id, resolved, outdated, path, line, comment_ids):
    return {"id": node_id, "isResolved": resolved, "isOutdated": outdated, "path": path, "line": line,
            "comments": {"nodes": [{"databaseId": cid} for cid in comment_ids]}}


def make_gateway():
    process = FakeProcessRunner()
    reviews, inline, issue = load("reviews.json"), load("inline.json"), load("issue_comments.json")
    process.script(["gh", "api", "repos/acme/webapp/pulls/984/reviews"], stdout=two_pages(reviews))
    process.script(["gh", "api", "repos/acme/webapp/pulls/984/comments"], stdout=two_pages(inline))
    process.script(["gh", "api", "repos/acme/webapp/issues/984/comments"], stdout=two_pages(issue))
    first = threads_page([thread("PRRT_1", False, False, "a.php", 57, [4088493200, 4088696316])], has_next=True)
    second = threads_page([thread("PRRT_2", True, True, "b.php", None, [4088493201])])
    process.script_sequence(["gh", "api", "graphql"], [
        __import__("review_loop.types.protocols", fromlist=["CompletedRun"]).CompletedRun([], 0, first, ""),
        __import__("review_loop.types.protocols", fromlist=["CompletedRun"]).CompletedRun([], 0, second, ""),
    ])
    return GhGitHub(process, cwd="/tmp"), process


def test_fetch_discussion_returns_all_surfaces_with_replies_and_thread_state_across_pages():
    gateway, process = make_gateway()

    discussion = gateway.fetch_discussion(REF)

    assert len(discussion.reviews) == 16
    assert len(discussion.inline) == 18
    assert len(discussion.issue_comments) == 6
    reply = next(comment for comment in discussion.inline if comment.id == 4088696316)
    assert reply.in_reply_to_id == 4088493200
    assert reply.path.endswith("logger.ts")
    root = next(comment for comment in discussion.inline if comment.id == 4088493200)
    assert (root.line, root.side, root.in_reply_to_id) == (57, "RIGHT", None)
    assert [thread.id for thread in discussion.threads] == ["PRRT_1", "PRRT_2"]
    assert discussion.threads[1].is_resolved and discussion.threads[1].is_outdated
    assert discussion.threads[0].comment_ids == [4088493200, 4088696316]
    graphql_calls = [call for call in process.calls if call["argv"][:3] == ["gh", "api", "graphql"]]
    assert len(graphql_calls) == 2
    assert all("--paginate" in call["argv"] and "--slurp" in call["argv"]
               for call in process.calls if call["argv"][2].startswith("repos/"))


def test_fetch_pull_maps_the_rest_shape_to_a_pull_request():
    process = FakeProcessRunner()
    process.script(["gh", "api", "repos/acme/webapp/pulls/984"], stdout=json.dumps({
        "number": 984, "title": "Consolidated fixes", "body": "Body text", "state": "open", "draft": True,
        "merged": False, "user": {"login": "vinlim"},
        "base": {"ref": "main", "sha": "b" * 40}, "head": {"ref": "claude/x", "sha": "h" * 40}}))
    gateway = GhGitHub(process, cwd="/tmp")

    pull = gateway.fetch_pull(REF)

    assert (pull.title, pull.author, pull.state, pull.draft, pull.merged) == ("Consolidated fixes", "vinlim", "open", True, False)
    assert (pull.base_ref, pull.base_sha, pull.head_ref, pull.head_sha) == ("main", "b" * 40, "claude/x", "h" * 40)


def test_a_failing_gh_call_raises_with_the_command_and_stderr():
    process = FakeProcessRunner()
    process.script(["gh", "api", "repos/acme/webapp/pulls/984"], exit_code=1, stderr="gh: Not Found (HTTP 404)")
    gateway = GhGitHub(process, cwd="/tmp")

    try:
        gateway.fetch_pull(REF)
    except RuntimeError as error:
        assert "pulls/984" in str(error) and "HTTP 404" in str(error)
    else:
        raise AssertionError("expected a RuntimeError")


def test_post_review_sends_one_request_with_inline_comments_and_returns_their_ids():
    process = FakeProcessRunner()
    process.script(["gh", "api", "-X", "POST", "repos/acme/webapp/pulls/984/reviews"], stdout=json.dumps({"id": 555}))
    process.script(["gh", "api", "repos/acme/webapp/pulls/984/reviews/555/comments"], stdout=json.dumps([[{"id": 71}, {"id": 72}]]))
    gateway = GhGitHub(process, cwd="/tmp")

    receipt = gateway.post_review(REF, "EXECUTIVE SUMMARY", "h" * 40, [
        {"path": "a.php", "line": 12, "body": "R1-F1 ..."}, {"path": "b.php", "line": 3, "body": "R1-F2 ..."}])

    assert receipt == {"id": 555, "comment_ids": [71, 72]}
    post = process.calls[0]
    payload = json.loads(post["stdin"])
    assert payload["event"] == "COMMENT" and payload["commit_id"] == "h" * 40 and payload["body"] == "EXECUTIVE SUMMARY"
    assert payload["comments"] == [{"path": "a.php", "line": 12, "side": "RIGHT", "body": "R1-F1 ..."},
                                   {"path": "b.php", "line": 3, "side": "RIGHT", "body": "R1-F2 ..."}]
    assert "--input" in post["argv"] and post["argv"][post["argv"].index("--input") + 1] == "-"


def test_reply_comment_edit_and_resolve_use_the_right_endpoints():
    process = FakeProcessRunner()
    process.script(["gh", "api", "-X", "POST", "repos/acme/webapp/pulls/984/comments/71/replies"], stdout=json.dumps({"id": 90}))
    process.script(["gh", "api", "-X", "POST", "repos/acme/webapp/issues/984/comments"], stdout=json.dumps({"id": 91}))
    process.script(["gh", "api", "-X", "PATCH", "repos/acme/webapp/issues/comments/91"], stdout=json.dumps({"id": 91}))
    process.script(["gh", "api", "graphql"], stdout=json.dumps({"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}))
    gateway = GhGitHub(process, cwd="/tmp")

    assert gateway.reply_to_comment(REF, 71, "Accepted.") == {"id": 90}
    assert gateway.post_comment(REF, "## Review response") == {"id": 91}
    assert gateway.edit_comment(REF, 91, "## Review response (edited)") == {"id": 91}
    assert gateway.resolve_thread("PRRT_1") == {"resolved": True}
    assert json.loads(process.calls[0]["stdin"]) == {"body": "Accepted."}
    assert json.loads(process.calls[2]["stdin"]) == {"body": "## Review response (edited)"}
    assert "PRRT_1" in " ".join(process.calls[3]["argv"])


def test_find_post_by_marker_searches_every_surface():
    gateway, process = make_gateway()

    found = gateway.find_post_by_marker(REF, "plain-object redaction in the logger")

    assert found is not None and found["kind"] == "issue_comment" and found["id"] == 5805354883
    assert gateway.find_post_by_marker(REF, "<!-- never-posted -->") is None


def test_inline_comment_ids_are_matched_by_marker_not_by_position():
    process = FakeProcessRunner()
    process.script(["gh", "api", "-X", "POST", "repos/acme/webapp/pulls/984/reviews"], stdout=json.dumps({"id": 555}))
    process.script(["gh", "api", "repos/acme/webapp/pulls/984/reviews/555/comments"], stdout=json.dumps([[
        {"id": 72, "body": "second <!-- review-loop finding=R1-F2 -->"}, {"id": 71, "body": "first <!-- review-loop finding=R1-F1 -->"}]]))
    gateway = GhGitHub(process, cwd="/tmp")

    receipt = gateway.post_review(REF, "body", "h" * 40, [
        {"path": "a.php", "line": 12, "body": "first <!-- review-loop finding=R1-F1 -->"},
        {"path": "b.php", "line": 3, "body": "second <!-- review-loop finding=R1-F2 -->"}])

    assert receipt["comment_ids"] == [71, 72]
