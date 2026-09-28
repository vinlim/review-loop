from review_loop.repositories import runs as runs_repo
from review_loop.repositories.db import connect, migrate
from review_loop.services.run_control import pause, resume, stop
from review_loop.types.run import Budgets, PauseReason, Run, RunState
from tests.fakes.clock import FakeClock
from tests.fakes.git import FakeGit


def make(state=RunState.REVIEWING):
    conn = connect(":memory:")
    migrate(conn)
    run = Run(id="webapp-1004-x", repo="webapp", pr_number=1004, pr_url="u", pr_author="vinlim", head_ref="claude/x",
              base_ref="main", head_sha="h" * 40, base_sha="b" * 40, merge_base_sha="m" * 40, state=state,
              budgets=Budgets(7, 2, 1), versions={}, worktree_path="/wt", created_at="t", updated_at="t")
    runs_repo.create_run(conn, run)
    return conn, run


def test_pause_keeps_the_run_resumable_at_the_state_it_left():
    conn, run = make()

    pause(conn, run, PauseReason.USAGE_LIMIT, FakeClock())
    stored = runs_repo.get_run(conn, run.id)
    assert (stored.state, stored.pause_reason, stored.resume_state) == (RunState.PAUSED, PauseReason.USAGE_LIMIT, RunState.REVIEWING)

    resumed = resume(conn, stored, FakeClock())
    assert resumed.ok
    stored = runs_repo.get_run(conn, run.id)
    assert (stored.state, stored.pause_reason, stored.resume_state) == (RunState.REVIEWING, None, None)


def test_resume_on_a_run_that_is_not_paused_is_refused():
    conn, run = make()

    assert not resume(conn, run, FakeClock()).ok


def test_stop_cancels_the_run_and_leaves_the_worktree_alone():
    conn, run = make()
    git = FakeGit()

    stop(conn, run, FakeClock())

    stored = runs_repo.get_run(conn, run.id)
    assert stored.state == RunState.CANCELLED and stored.worktree_path == "/wt"
    assert git.calls == []


def test_resuming_after_the_head_moved_goes_back_through_preparation_instead_of_repeating_the_check():
    conn, run = make(RunState.VERIFYING)

    pause(conn, run, PauseReason.HEAD_CHANGED, FakeClock())
    resumed = resume(conn, runs_repo.get_run(conn, run.id), FakeClock()).value

    assert resumed.state == RunState.PREPARING


def test_a_run_paused_for_inspection_before_modes_existed_stays_inspect_only():
    from review_loop.services.phase_support import Deps, inspect_only

    conn, run = make(RunState.PAUSED)
    run.pause_reason = PauseReason.INSPECT_ONLY
    run.extra = {}

    class NotInspecting:
        inspect_only = False

    assert inspect_only(NotInspecting(), run) is True
