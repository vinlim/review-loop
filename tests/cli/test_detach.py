"""A run outlives the shell that started it: start and resume can hand it to a detached coordinator, and a crash pauses it."""

import subprocess
import sys

from review_loop.cli.main import main
from review_loop.repositories import runs as runs_repo
from review_loop.types.run import PauseReason, RunState
from tests.cli.test_main import URL, container


class FakeSpawner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, cwd, env, log_path):
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": dict(env), "log_path": str(log_path)})
        return 4242


def detachable(settings):
    from tests.services.test_preflight import agents

    box = container(settings)
    box.spawn = FakeSpawner()
    box.agents = agents()
    return box


def test_start_detach_enrols_the_run_and_hands_it_to_a_coordinator_in_its_own_session(settings, capsys):
    box = detachable(settings)

    assert main(["start", URL, "--detach"], container=box) == 0

    run_id = runs_repo.list_runs(box.conn)[0].id
    out = capsys.readouterr().out
    assert f"run {run_id} (preparing, publish) for {URL}" in out.splitlines()[0]
    assert "coordinator pid 4242" in out and f"review-loop wait {run_id}" in out
    call = box.spawn.calls[0]
    assert call["argv"] == [sys.executable, "-m", "review_loop.cli.main", "drive", run_id]
    assert call["log_path"] == str(settings.state_dir / "runs" / run_id / "coordinator.log")
    assert runs_repo.get_run(box.conn, run_id).state == RunState.PREPARING


def test_the_detached_coordinator_is_the_only_process_given_the_claimed_claude_login(settings, monkeypatch):
    box = detachable(settings)
    box.claude_oauth_token = "author-token"
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    main(["start", URL, "--detach"], container=box)

    assert box.spawn.calls[0]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "author-token"


def test_resume_detach_hands_a_paused_run_to_a_detached_coordinator(settings, capsys):
    box = detachable(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    main(["pause", run_id], container=box)
    capsys.readouterr()

    assert main(["resume", run_id, "--detach"], container=box) == 0

    out = capsys.readouterr().out
    assert f"resumed {run_id} at preparing" in out and "coordinator pid 4242" in out
    assert box.spawn.calls[0]["argv"][-2:] == ["drive", run_id]


def test_drive_runs_an_enrolled_run_in_this_process_to_its_end(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    box.github.add_pull(1004, state="closed")
    capsys.readouterr()

    assert main(["drive", run_id], container=box) == 1

    assert runs_repo.get_run(box.conn, run_id).state == RunState.CANCELLED
    assert f"run {run_id}: cancelled" in capsys.readouterr().out


def test_drive_refuses_a_run_that_is_not_in_a_working_state(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    main(["pause", run_id], container=box)
    capsys.readouterr()

    assert main(["drive", run_id], container=box) == 2

    assert "paused" in capsys.readouterr().err and runs_repo.get_run(box.conn, run_id).state == RunState.PAUSED


def test_a_coordinator_crash_pauses_the_run_with_the_traceback_recorded_for_show(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id

    def explode(ref):
        raise RuntimeError("boom: the GitHub client lost its socket")

    box.github.fetch_pull = explode
    capsys.readouterr()

    assert main(["drive", run_id], container=box) == 1

    run = runs_repo.get_run(box.conn, run_id)
    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.COORDINATOR_FAILED and run.resume_state == RunState.PREPARING
    captured = capsys.readouterr()
    assert f"run {run_id}: paused (coordinator_failed)" in captured.out and "RuntimeError: boom" in captured.err
    main(["show", run_id], container=box)
    assert "coordinator failure" in capsys.readouterr().out and "boom: the GitHub client lost its socket" in run.extra["coordinator_failure"]


def test_the_cli_runs_as_a_module_which_is_how_a_detached_coordinator_starts():
    completed = subprocess.run([sys.executable, "-m", "review_loop.cli.main", "--help"], capture_output=True, text=True)

    assert completed.returncode == 0 and "drive" in completed.stdout
