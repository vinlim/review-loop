"""Waiting on a run someone else drives: report each transition, stop at a stop, and notice a coordinator that is gone."""

from review_loop.repositories import runs as runs_repo
from review_loop.repositories.db import connect, migrate
from review_loop.services.wait import wait_for_run
from review_loop.types.run import Budgets, PauseReason, Run, RunState


def make(state=RunState.REVIEWING):
    conn = connect(":memory:")
    migrate(conn)
    run = Run(id="webapp-1004-x", repo="webapp", pr_number=1004, pr_url="u", pr_author="vinlim", head_ref="claude/x",
              base_ref="main", head_sha="h" * 40, base_sha="b" * 40, merge_base_sha="m" * 40, state=state,
              budgets=Budgets(7, 2, 1), versions={}, worktree_path="/wt", created_at="t", updated_at="t")
    runs_repo.create_run(conn, run)
    return conn, run


def advance(conn, run, states):
    """A sleep that moves the run one state per call, the way a coordinator in another process would."""
    pending = list(states)

    def sleep(seconds):
        if pending:
            run.state = pending.pop(0)
            runs_repo.save_run(conn, run)

    return sleep


def test_wait_reports_each_transition_and_returns_when_the_run_completes():
    conn, run = make()
    seen = []

    final = wait_for_run(conn, run.id, lock_held=lambda run: True, sleep=advance(conn, run, [RunState.ASSESSING, RunState.COMPLETE]),
                         on_step=lambda run: seen.append(run.state))

    assert final.state == RunState.COMPLETE
    assert seen == [RunState.ASSESSING, RunState.COMPLETE]


def test_wait_returns_when_the_run_pauses():
    conn, run = make()

    def pause(seconds):
        run.state, run.pause_reason = RunState.PAUSED, PauseReason.USAGE_LIMIT
        runs_repo.save_run(conn, run)

    final = wait_for_run(conn, run.id, lock_held=lambda run: True, sleep=pause, on_step=lambda run: None)

    assert final.state == RunState.PAUSED and final.pause_reason == PauseReason.USAGE_LIMIT


def test_wait_tolerates_a_lock_that_is_briefly_free_but_returns_once_no_coordinator_comes_back():
    conn, run = make()
    polls = []
    held = iter([False, True, False, False, False, False])

    final = wait_for_run(conn, run.id, lock_held=lambda run: next(held), sleep=lambda seconds: polls.append(seconds), on_step=lambda run: None)

    assert final.state == RunState.REVIEWING
    assert len(polls) == 4, "three consecutive free polls end the wait; the single free poll before did not"


def test_wait_on_an_unknown_run_returns_nothing():
    conn, run = make()

    assert wait_for_run(conn, "no-such-run", lock_held=lambda run: True, sleep=lambda seconds: None, on_step=lambda run: None) is None
