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


def test_resume_continues_an_unattended_run_from_the_phase_its_coordinator_left():
    conn, run = make(RunState.VERIFYING)

    resumed = resume(conn, run, FakeClock(), unattended=True)

    assert resumed.ok and resumed.value.state == RunState.VERIFYING
    assert runs_repo.get_run(conn, run.id).state == RunState.VERIFYING


def test_resume_without_the_unattended_signal_still_refuses_a_run_that_is_not_paused():
    conn, run = make(RunState.VERIFYING)

    assert not resume(conn, run, FakeClock(), unattended=False).ok


def test_a_finished_run_is_never_resumed_even_when_nothing_holds_its_lock():
    conn, run = make(RunState.COMPLETE)

    assert not resume(conn, run, FakeClock(), unattended=True).ok


# --- an external stop or manual pause wins over any pause the coordinator records afterwards -------------------

def test_a_coordinator_pause_never_overwrites_a_stop_that_landed_meanwhile():
    conn, run = make()
    stale = runs_repo.get_run(conn, run.id)
    stop(conn, run, FakeClock())

    returned = pause(conn, stale, PauseReason.AGENT_UNAVAILABLE, FakeClock())

    assert returned.state == RunState.CANCELLED
    assert runs_repo.get_run(conn, run.id).state == RunState.CANCELLED


def test_a_coordinator_pause_never_overwrites_a_manual_pause_that_landed_meanwhile():
    conn, run = make()
    stale = runs_repo.get_run(conn, run.id)
    pause(conn, run, PauseReason.MANUAL, FakeClock())

    returned = pause(conn, stale, PauseReason.CHECKS_FAILED, FakeClock())

    assert (returned.state, returned.pause_reason) == (RunState.PAUSED, PauseReason.MANUAL)
    assert runs_repo.get_run(conn, run.id).pause_reason == PauseReason.MANUAL


def test_a_manual_pause_still_applies_to_a_run_the_coordinator_paused():
    conn, run = make()
    pause(conn, run, PauseReason.AGENT_FAILED, FakeClock())

    pause(conn, runs_repo.get_run(conn, run.id), PauseReason.MANUAL, FakeClock())

    assert runs_repo.get_run(conn, run.id).pause_reason == PauseReason.MANUAL


def test_resume_is_refused_when_the_run_changed_between_the_read_and_the_write():
    conn, run = make()
    pause(conn, run, PauseReason.USAGE_LIMIT, FakeClock())
    stale = runs_repo.get_run(conn, run.id)
    stop(conn, runs_repo.get_run(conn, run.id), FakeClock())

    result = resume(conn, stale, FakeClock())

    assert not result.ok and "cancelled" in result.error
    assert runs_repo.get_run(conn, run.id).state == RunState.CANCELLED


def test_resume_of_an_unattended_run_is_refused_when_a_stop_landed_after_the_read():
    conn, run = make(RunState.VERIFYING)
    stale = runs_repo.get_run(conn, run.id)
    stop(conn, runs_repo.get_run(conn, run.id), FakeClock())

    result = resume(conn, stale, FakeClock(), unattended=True)

    assert not result.ok and runs_repo.get_run(conn, run.id).state == RunState.CANCELLED


# --- a person's pause or stop changes only the control columns, never the progress a coordinator persisted -------

def coordinator_advanced(conn, run):
    """What a coordinator in another process persists between a controller's read and its write."""
    fresh = runs_repo.get_run(conn, run.id)
    fresh.state, fresh.pass_no, fresh.head_sha = RunState.ASSESSING, 2, "n" * 40
    fresh.extra = {"pending_review_pass": 2, "remote_head": "n" * 40}
    runs_repo.save_run(conn, fresh)


def test_a_manual_pause_keeps_the_progress_the_coordinator_persisted_after_the_controller_read_the_run():
    conn, run = make()
    stale = runs_repo.get_run(conn, run.id)
    coordinator_advanced(conn, run)

    returned = pause(conn, stale, PauseReason.MANUAL, FakeClock())

    stored = runs_repo.get_run(conn, run.id)
    assert (stored.state, stored.pause_reason, stored.resume_state) == (RunState.PAUSED, PauseReason.MANUAL, RunState.ASSESSING)
    assert (stored.pass_no, stored.head_sha, stored.extra) == (2, "n" * 40, {"pending_review_pass": 2, "remote_head": "n" * 40})
    assert returned.resume_state == RunState.ASSESSING and returned.pass_no == 2


def test_a_stop_keeps_the_progress_and_history_the_coordinator_persisted_after_the_controller_read_the_run():
    conn, run = make()
    stale = runs_repo.get_run(conn, run.id)
    coordinator_advanced(conn, run)

    returned = stop(conn, stale, FakeClock()).value

    stored = runs_repo.get_run(conn, run.id)
    assert (stored.state, stored.pause_reason, stored.resume_state) == (RunState.CANCELLED, None, None)
    assert (stored.pass_no, stored.head_sha, stored.extra["remote_head"]) == (2, "n" * 40, "n" * 40)
    assert returned.state == RunState.CANCELLED and returned.pass_no == 2


def test_a_manual_pause_on_an_already_paused_run_keeps_where_it_will_resume():
    conn, run = make(RunState.VERIFYING)
    pause(conn, run, PauseReason.CHECKS_FAILED, FakeClock())

    pause(conn, runs_repo.get_run(conn, run.id), PauseReason.MANUAL, FakeClock())

    stored = runs_repo.get_run(conn, run.id)
    assert (stored.pause_reason, stored.resume_state) == (PauseReason.MANUAL, RunState.VERIFYING)


# --- a finished run stays finished: a person's pause or stop applies only to a working or paused run -----------

def test_a_stop_on_a_complete_run_is_not_applied_and_keeps_its_outcome():
    from review_loop.types.run import Outcome

    conn, run = make(RunState.COMPLETE)
    run.outcome = Outcome.COMPLETE_WITH_EXCEPTIONS
    runs_repo.save_run(conn, run)

    result = stop(conn, runs_repo.get_run(conn, run.id), FakeClock())

    assert not result.ok and result.error.state == RunState.COMPLETE
    stored = runs_repo.get_run(conn, run.id)
    assert (stored.state, stored.outcome) == (RunState.COMPLETE, Outcome.COMPLETE_WITH_EXCEPTIONS)


def test_a_manual_pause_on_a_stopped_run_is_not_applied_so_the_run_never_counts_as_active_again():
    from review_loop.services.run_control import pause_by_hand

    conn, run = make()
    stop(conn, run, FakeClock())

    result = pause_by_hand(conn, runs_repo.get_run(conn, run.id), FakeClock())

    assert not result.ok and result.error.state == RunState.CANCELLED
    assert runs_repo.get_run(conn, run.id).state == RunState.CANCELLED
    assert runs_repo.find_active_run(conn, "webapp", 1004) is None


def test_a_stop_on_a_paused_run_applies():
    conn, run = make()
    pause(conn, run, PauseReason.CHECKS_FAILED, FakeClock())

    result = stop(conn, runs_repo.get_run(conn, run.id), FakeClock())

    assert result.ok and result.value.state == RunState.CANCELLED


def test_a_head_changed_pause_resumes_where_it_paused_when_the_head_is_known_not_to_have_moved():
    conn, run = make(RunState.PUBLISHING)
    pause(conn, run, PauseReason.HEAD_CHANGED, FakeClock())

    resumed = resume(conn, runs_repo.get_run(conn, run.id), FakeClock(), head_unchanged=True).value

    assert resumed.state == RunState.PUBLISHING
