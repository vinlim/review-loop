from review_loop.repositories import outbox as outbox_repo
from review_loop.repositories.db import connect, migrate
from review_loop.services.publication import Publisher
from review_loop.types.pull_request import PullRef
from tests.fakes.clock import FakeClock
from tests.fakes.github import FakeGitHub

REF = PullRef("acme", "webapp", 1004)


def make():
    conn = connect(":memory:")
    migrate(conn)
    github = FakeGitHub()
    github.add_pull(1004)
    return conn, github, Publisher(conn, github, FakeClock())


def test_the_intent_is_recorded_before_the_gateway_is_called_and_the_receipt_after():
    conn, github, publisher = make()
    seen_at_call = []
    github.on_call = lambda name: seen_at_call.append([dict(row) for row in outbox_repo.list_entries(conn, "run-1")])

    receipt = publisher.perform("run-1", "comment", {"body": "hello <!-- m1 -->"}, marker="<!-- m1 -->",
                                operation=lambda: github.post_comment(REF, "hello <!-- m1 -->"))

    assert seen_at_call[0][0]["state"] == "intended"
    entries = outbox_repo.list_entries(conn, "run-1")
    assert entries[0]["state"] == "done" and entries[0]["receipt"]["id"] == receipt["id"]


def test_an_operation_that_raises_leaves_the_entry_uncertain_and_re_raises():
    conn, github, publisher = make()

    def explode():
        raise TimeoutError("gh timed out")

    try:
        publisher.perform("run-1", "comment", {"body": "x"}, marker="<!-- m2 -->", operation=explode)
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected the error to propagate")

    assert outbox_repo.list_entries(conn, "run-1")[0]["state"] == "uncertain"


def test_on_restart_an_uncertain_entry_is_reconciled_by_marker_before_any_resend():
    conn, github, publisher = make()
    outbox_repo.record_intent(conn, "run-1", "comment", {"body": "x <!-- m3 -->"}, "<!-- m3 -->", "2026-09-27T10:00:00+00:00")
    outbox_repo.mark_uncertain(conn, 1)
    landed = github.add_comment(1004, "x <!-- m3 -->")
    outbox_repo.record_intent(conn, "run-1", "comment", {"body": "y <!-- m4 -->"}, "<!-- m4 -->", "2026-09-27T10:01:00+00:00")
    outbox_repo.mark_uncertain(conn, 2)

    outcomes = publisher.reconcile("run-1", REF)

    entries = {entry["id"]: entry for entry in outbox_repo.list_entries(conn, "run-1")}
    assert entries[1]["state"] == "done" and entries[1]["receipt"]["id"] == landed
    assert entries[2]["state"] == "unsent"
    assert github.writes == []
    assert outcomes == [(1, "done"), (2, "unsent")]
