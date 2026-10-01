from review_loop.cli.container import Container
from review_loop.cli.main import main
from review_loop.repositories import runs as runs_repo
from review_loop.repositories.db import connect, migrate
from review_loop.services import run_control
from review_loop.types.run import PauseReason
from tests.config.test_settings import MINIMAL, write
from tests.fakes.clock import FakeClock
from tests.fakes.git import FakeGit
from tests.fakes.github import FakeGitHub
from tests.fakes.process import FakeProcessRunner

URL = "https://github.com/acme/webapp/pull/1004"


def container(settings):
    conn = connect(":memory:")
    migrate(conn)
    github = FakeGitHub()
    github.add_pull(1004)
    return Container(settings=settings, conn=conn, process=FakeProcessRunner(), git=FakeGit(), github=github,
                     clock=FakeClock(), versions={"review-loop": "0.1.0"})


def test_start_then_status_then_show_round_trip_through_the_cli(settings, capsys):
    box = container(settings)

    assert main(["start", URL, "--no-run"], container=box) == 0
    run_id = runs_repo.list_runs(box.conn)[0].id
    assert run_id in capsys.readouterr().out

    assert main(["status"], container=box) == 0
    assert "preparing" in capsys.readouterr().out

    assert main(["show", run_id], container=box) == 0
    assert "h" * 40 in capsys.readouterr().out


def test_pause_resume_and_stop_change_the_run_state(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id

    assert main(["pause", run_id], container=box) == 0
    assert runs_repo.get_run(box.conn, run_id).state.value == "paused"
    assert main(["resume", run_id, "--no-run"], container=box) == 0
    assert runs_repo.get_run(box.conn, run_id).state.value == "preparing"
    assert main(["stop", run_id], container=box) == 0
    assert runs_repo.get_run(box.conn, run_id).state.value == "cancelled"


def test_a_refused_start_explains_why_and_exits_non_zero(settings, capsys):
    box = container(settings)
    box.github.add_pull(1004, author="stranger")

    assert main(["start", URL, "--no-run"], container=box) == 2
    assert "author_not_allowed" in capsys.readouterr().err


def test_a_config_mistake_exits_non_zero_naming_the_key_without_advice_to_register(tmp_path, monkeypatch, capsys):
    write(tmp_path, MINIMAL + '\n[repositories.webapp.review]\nreviwer = "agy"\n')
    monkeypatch.setenv("REVIEW_LOOP_HOME", str(tmp_path))

    assert main(["status"]) == 2
    err = capsys.readouterr().err
    assert "repositories.webapp.review.reviwer" in err
    assert "repo add" not in err


def test_a_missing_config_exits_non_zero_advising_to_register_a_repository(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_LOOP_HOME", str(tmp_path))

    assert main(["status"]) == 2
    assert "review-loop repo add <path>" in capsys.readouterr().err


def test_a_start_in_the_other_mode_is_refused_and_names_the_run_to_stop(settings, capsys):
    box = container(settings)
    assert main(["start", URL, "--no-run", "--inspect-only"], container=box) == 0
    run_id = runs_repo.list_runs(box.conn)[0].id
    capsys.readouterr()

    assert main(["start", URL, "--no-run"], container=box) == 2

    err = capsys.readouterr().err
    assert "mode_conflict" in err and run_id in err and "is inspect" in err and "stop it" in err
    assert [run.id for run in runs_repo.list_runs(box.conn)] == [run_id]


def test_the_start_line_reports_the_resolved_mode_for_a_run_without_a_stored_one(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run = runs_repo.list_runs(box.conn)[0]
    run.extra.pop("mode")
    run_control.pause(box.conn, run, PauseReason.INSPECT_ONLY, box.clock)
    capsys.readouterr()

    assert main(["start", URL, "--no-run", "--inspect-only"], container=box) == 0

    assert f"run {run.id} (paused, inspect)" in capsys.readouterr().out


def test_main_claims_the_claude_login_before_any_command_runs(settings, monkeypatch, capsys):
    import os

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "author-token")
    box = container(settings)

    assert main(["status"], container=box) == 0

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ


def test_status_and_show_say_when_no_coordinator_is_driving_an_enrolled_run(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    capsys.readouterr()

    assert main(["status"], container=box) == 0
    assert "preparing (no coordinator)" in capsys.readouterr().out
    assert main(["show", run_id], container=box) == 0
    assert "no coordinator; resume continues from this phase" in capsys.readouterr().out


def test_status_does_not_call_a_run_unattended_while_a_coordinator_holds_its_lock(settings, capsys):
    from review_loop.services.locks import RunLock

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    capsys.readouterr()
    lock = RunLock(settings.state_dir, "webapp", 1004)
    lock.acquire()
    try:
        assert main(["status"], container=box) == 0
    finally:
        lock.release()

    assert "no coordinator" not in capsys.readouterr().out


def test_resume_continues_an_unattended_run_in_place(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    capsys.readouterr()

    assert main(["resume", run_id, "--no-run"], container=box) == 0

    assert f"resumed {run_id} at preparing" in capsys.readouterr().out


def test_wait_exits_one_naming_the_missing_coordinator_for_an_unattended_run(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    capsys.readouterr()

    assert main(["wait", run_id, "--interval", "0"], container=box) == 1

    assert f"run {run_id}: preparing (no coordinator)" in capsys.readouterr().out


def test_wait_exits_zero_for_a_complete_run_and_two_for_an_unknown_one(settings, capsys):
    from review_loop.types.run import RunState

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run = runs_repo.list_runs(box.conn)[0]
    run.state = RunState.COMPLETE
    runs_repo.save_run(box.conn, run)
    capsys.readouterr()

    assert main(["wait", run.id, "--interval", "0"], container=box) == 0
    assert f"run {run.id}: complete" in capsys.readouterr().out
    assert main(["wait", "nope", "--interval", "0"], container=box) == 2


def test_pause_refuses_a_stopped_run_naming_its_state_and_a_later_start_gets_a_fresh_run(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    main(["stop", run_id], container=box)
    capsys.readouterr()

    assert main(["pause", run_id], container=box) == 2

    assert "is cancelled" in capsys.readouterr().err
    assert runs_repo.get_run(box.conn, run_id).state.value == "cancelled"
    assert main(["start", URL, "--no-run"], container=box) == 0
    assert len(runs_repo.list_runs(box.conn)) == 2


def test_stop_refuses_a_complete_run_naming_its_state(settings, capsys):
    from review_loop.types.run import RunState

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run = runs_repo.list_runs(box.conn)[0]
    run.state = RunState.COMPLETE
    runs_repo.save_run(box.conn, run)
    capsys.readouterr()

    assert main(["stop", run.id], container=box) == 2

    assert "is complete" in capsys.readouterr().err
    assert runs_repo.get_run(box.conn, run.id).state == RunState.COMPLETE


def test_status_and_show_flag_a_paused_run_whose_coordinator_still_holds_the_lock(settings, capsys):
    from review_loop.services.locks import RunLock

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    main(["pause", run_id], container=box)
    capsys.readouterr()
    lock = RunLock(settings.state_dir, "webapp", 1004)
    lock.acquire()
    try:
        main(["status"], container=box)
        assert "paused (manual) (coordinator still finishing its phase)" in capsys.readouterr().out
        main(["show", run_id], container=box)
        assert "coordinator still finishing its phase" in capsys.readouterr().out
    finally:
        lock.release()

    main(["status"], container=box)
    assert "finishing" not in capsys.readouterr().out


def test_resume_after_a_head_changed_pause_continues_where_it_paused_when_git_shows_the_head_did_not_move(settings, capsys):
    from review_loop.types.run import RunState

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run = runs_repo.list_runs(box.conn)[0]
    run.state = RunState.PUBLISHING
    runs_repo.save_run(box.conn, run)
    run_control.pause(box.conn, run, PauseReason.HEAD_CHANGED, box.clock)
    box.git.remote_heads[run.head_ref] = run.extra["remote_head"]  # git: the branch still points where the run left it
    capsys.readouterr()

    assert main(["resume", run.id, "--no-run"], container=box) == 0

    assert f"resumed {run.id} at publishing" in capsys.readouterr().out


def test_resume_after_a_head_changed_pause_goes_back_through_preparation_when_git_shows_a_move(settings, capsys):
    from review_loop.types.run import RunState

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run = runs_repo.list_runs(box.conn)[0]
    run.state = RunState.PUBLISHING
    runs_repo.save_run(box.conn, run)
    run_control.pause(box.conn, run, PauseReason.HEAD_CHANGED, box.clock)
    box.git.remote_heads[run.head_ref] = "9" * 40
    capsys.readouterr()

    assert main(["resume", run.id, "--no-run"], container=box) == 0

    assert f"resumed {run.id} at preparing" in capsys.readouterr().out


def test_resume_after_checks_failed_goes_back_through_preparation_once_the_branch_moved(settings, capsys):
    from review_loop.types.run import RunState

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run = runs_repo.list_runs(box.conn)[0]
    run.state = RunState.VERIFYING
    runs_repo.save_run(box.conn, run)
    run_control.pause(box.conn, run, PauseReason.CHECKS_FAILED, box.clock)
    box.git.remote_heads[run.head_ref] = "e" * 40  # the base fix was merged into the PR branch
    capsys.readouterr()

    assert main(["resume", run.id, "--no-run"], container=box) == 0

    assert f"resumed {run.id} at preparing" in capsys.readouterr().out


def test_resume_never_counts_the_runs_own_unrecorded_push_as_a_move(settings, capsys):
    from review_loop.types.run import RunState

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run = runs_repo.list_runs(box.conn)[0]
    run.state = RunState.VERIFYING
    run.extra[f"candidate_commit_pass_{run.pass_no}"] = "c" * 40
    runs_repo.save_run(box.conn, run)
    box.git.remote_heads[run.head_ref] = "c" * 40  # pushed, then the coordinator died before recording it
    capsys.readouterr()

    assert main(["resume", run.id, "--no-run"], container=box) == 0

    assert f"resumed {run.id} at verifying" in capsys.readouterr().out
