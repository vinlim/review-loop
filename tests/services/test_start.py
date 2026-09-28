from review_loop.repositories.db import connect, migrate
from review_loop.repositories import runs as runs_repo
from review_loop.services.start import StartRefusal, start_run
from review_loop.types.run import RunState
from tests.fakes.clock import FakeClock
from tests.fakes.git import FakeGit
from tests.fakes.github import FakeGitHub
from tests.services.test_coordinator_phases import with_review

URL = "https://github.com/acme/webapp/pull/1004"


def make(settings):
    conn = connect(":memory:")
    migrate(conn)
    github = FakeGitHub()
    github.add_pull(1004, head_sha="a" * 40, base_sha="b" * 40)
    git = FakeGit()
    git.merge_bases[("b" * 40, "a" * 40)] = "c" * 40
    return conn, github, git


def test_start_creates_a_run_in_preparing_with_head_base_merge_base_budgets_and_versions(settings):
    conn, github, git = make(settings)

    result = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(),
                       versions={"review-loop": "0.1.0", "codex": "0.157.1", "claude": "2.1.278"})

    assert result.ok
    run = result.value
    assert run.state == RunState.PREPARING
    assert (run.head_sha, run.base_sha, run.merge_base_sha) == ("a" * 40, "b" * 40, "c" * 40)
    assert (run.budgets.max_review_passes, run.budgets.max_fix_attempts, run.budgets.max_alignment_exchanges) == (7, 2, 1)
    assert run.versions["codex"] == "0.157.1"
    assert runs_repo.get_run(conn, run.id) == run


def test_a_second_start_for_the_same_pr_returns_the_active_run_and_creates_nothing(settings):
    conn, github, git = make(settings)
    first = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={}).value

    second = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={})

    assert second.ok and second.value.id == first.id
    assert len(runs_repo.list_runs(conn)) == 1


def test_a_pr_by_an_author_outside_allowed_pr_authors_is_refused(settings):
    conn, github, git = make(settings)
    github.add_pull(1004, author="someone-else", head_sha="a" * 40, base_sha="b" * 40)

    result = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={})

    assert not result.ok
    assert result.error == StartRefusal.AUTHOR_NOT_ALLOWED
    assert runs_repo.list_runs(conn) == []


def test_a_pr_in_a_repository_that_is_not_registered_is_refused(settings):
    conn, github, git = make(settings)

    result = start_run("https://github.com/acme/other/pull/1", settings=settings, conn=conn, github=github,
                       git=git, clock=FakeClock(), versions={})

    assert not result.ok
    assert result.error == StartRefusal.REPOSITORY_NOT_REGISTERED


def test_an_explicit_author_session_is_refused_when_the_author_agent_cannot_fork_it(settings):
    conn, github, git = make(settings)

    result = start_run(URL, settings=with_review(settings, author="agy"), conn=conn, github=github, git=git, clock=FakeClock(),
                       versions={}, author_session="conv-123")

    assert not result.ok and result.error == StartRefusal.AUTHOR_SESSION_NOT_FORKABLE


def test_session_discovery_is_allowed_for_an_author_that_cannot_fork(settings):
    conn, github, git = make(settings)

    result = start_run(URL, settings=with_review(settings, author="agy"), conn=conn, github=github, git=git, clock=FakeClock(), versions={})

    assert result.ok and result.value.author_session == "auto"
