import pytest

from review_loop.repositories.db import connect, migrate
from review_loop.repositories import runs as runs_repo
from review_loop.services.start import StartRefusal, start_run
from review_loop.services import run_control
from review_loop.types.run import AgentChoice, PauseReason, RunState
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


def test_start_creates_a_run_in_preparing_with_head_base_merge_base_budgets_versions_and_agents(settings):
    conn, github, git = make(settings)

    result = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(),
                       versions={"review-loop": "0.1.0", "codex": "0.157.1", "claude": "2.1.278"})

    assert result.ok
    run = result.value
    assert run.state == RunState.PREPARING
    assert (run.head_sha, run.base_sha, run.merge_base_sha) == ("a" * 40, "b" * 40, "c" * 40)
    assert (run.budgets.max_review_passes, run.budgets.max_fix_attempts, run.budgets.max_alignment_exchanges) == (7, 2, 1)
    assert run.versions["codex"] == "0.157.1"
    assert run.agents == {"reviewer": AgentChoice("codex", "gpt-5.6-sol", "xhigh"), "author": AgentChoice("claude", "claude-opus-5-5", "xhigh")}
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


@pytest.mark.parametrize("first_inspect, second_inspect", [(True, False), (False, True)])
def test_a_start_in_the_other_mode_than_the_active_run_is_refused_and_creates_nothing(settings, first_inspect, second_inspect):
    conn, github, git = make(settings)
    first = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={},
                      inspect_only=first_inspect).value

    result = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={},
                       inspect_only=second_inspect)

    assert not result.ok and result.error == StartRefusal.MODE_CONFLICT
    assert [run.id for run in runs_repo.list_runs(conn)] == [first.id]
    assert runs_repo.get_run(conn, first.id).extra["mode"] == first.extra["mode"]


def test_a_second_inspect_only_start_reuses_the_active_dry_run(settings):
    conn, github, git = make(settings)
    first = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={},
                      inspect_only=True).value

    second = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={},
                       inspect_only=True)

    assert second.ok and second.value.id == first.id


def test_a_run_without_a_stored_mode_that_paused_for_inspection_is_reused_by_inspect_only_and_refused_by_publish(settings):
    conn, github, git = make(settings)
    run = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={}).value
    run.extra.pop("mode")
    run_control.pause(conn, run, PauseReason.INSPECT_ONLY, FakeClock())

    reused = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={}, inspect_only=True)
    refused = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=FakeClock(), versions={})

    assert reused.ok and reused.value.id == run.id
    assert not refused.ok and refused.error == StartRefusal.MODE_CONFLICT


def test_a_stop_and_a_start_within_the_same_second_get_distinct_ids(settings):
    conn, github, git = make(settings)
    clock = FakeClock()
    first = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=clock, versions={}, inspect_only=True).value
    run_control.stop(conn, first, clock)

    second = start_run(URL, settings=settings, conn=conn, github=github, git=git, clock=clock, versions={})

    assert second.ok and second.value.id == f"{first.id}-2"
    assert second.value.extra["mode"] == "publish"
    assert len(runs_repo.list_runs(conn)) == 2
