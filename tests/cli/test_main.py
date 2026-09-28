from review_loop.cli.container import Container
from review_loop.cli.main import main
from review_loop.repositories import runs as runs_repo
from review_loop.repositories.db import connect, migrate
from review_loop.services import run_control
from review_loop.types.run import PauseReason
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
