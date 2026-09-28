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
    box.process.script(["git", "init"])
    return box


def test_start_detach_enrols_the_run_and_hands_it_to_a_coordinator_in_its_own_session(settings, capsys):
    box = detachable(settings)

    assert main(["start", URL, "--detach"], container=box) == 0

    run_id = runs_repo.list_runs(box.conn)[0].id
    out = capsys.readouterr().out
    assert f"run {run_id} (preparing, publish) for {URL}" in out.splitlines()[0]
    assert "coordinator pid 4242" in out and f"review-loop wait {run_id}" in out
    call = box.spawn.calls[0]
    assert call["argv"] == [sys.executable, "-m", "review_loop.cli.main", "drive", run_id, "--no-preflight"]
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
    assert box.spawn.calls[0]["argv"][-3:] == ["drive", run_id, "--no-preflight"]


def test_drive_runs_an_enrolled_run_in_this_process_to_its_end(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    box.github.add_pull(1004, state="closed")
    capsys.readouterr()

    assert main(["drive", run_id, "--no-preflight"], container=box) == 1

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

    assert main(["drive", run_id, "--no-preflight"], container=box) == 1

    run = runs_repo.get_run(box.conn, run_id)
    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.COORDINATOR_FAILED and run.resume_state == RunState.PREPARING
    captured = capsys.readouterr()
    assert f"run {run_id}: paused (coordinator_failed)" in captured.out and "RuntimeError: boom" in captured.err
    main(["show", run_id], container=box)
    assert "coordinator failure" in capsys.readouterr().out and "boom: the GitHub client lost its socket" in run.extra["coordinator_failure"]


def test_the_cli_runs_as_a_module_which_is_how_a_detached_coordinator_starts():
    completed = subprocess.run([sys.executable, "-m", "review_loop.cli.main", "--help"], capture_output=True, text=True)

    assert completed.returncode == 0 and "drive" in completed.stdout


# --- the lock covers the decision to resume or drive, not just the drive -------------------------------------

def test_resume_refuses_without_touching_the_run_while_a_coordinator_holds_its_lock(settings, capsys):
    from review_loop.services.locks import RunLock

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    main(["pause", run_id], container=box)
    capsys.readouterr()
    lock = RunLock(settings.state_dir, "webapp", 1004)
    lock.acquire()
    try:
        assert main(["resume", run_id], container=box) == 3
    finally:
        lock.release()

    assert runs_repo.get_run(box.conn, run_id).state == RunState.PAUSED
    assert f"review-loop wait {run_id}" in capsys.readouterr().err


def test_resume_decides_from_the_run_as_it_is_once_the_lock_is_held(settings, capsys, monkeypatch):
    """Another coordinator may finish the run between the read and the lock: the resume must see that, not its stale copy."""
    from review_loop.services.locks import RunLock

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    main(["pause", run_id], container=box)
    original_acquire = RunLock.acquire

    def finish_then_acquire(self):
        run = runs_repo.get_run(box.conn, run_id)
        run.state, run.pause_reason, run.resume_state = RunState.COMPLETE, None, None
        runs_repo.save_run(box.conn, run)
        original_acquire(self)

    monkeypatch.setattr(RunLock, "acquire", finish_then_acquire)
    capsys.readouterr()

    assert main(["resume", run_id], container=box) == 2

    assert runs_repo.get_run(box.conn, run_id).state == RunState.COMPLETE
    assert "is complete" in capsys.readouterr().err


def test_drive_decides_from_the_run_as_it_is_once_the_lock_is_held(settings, capsys, monkeypatch):
    from review_loop.services.locks import RunLock

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    original_acquire = RunLock.acquire

    def finish_then_acquire(self):
        run = runs_repo.get_run(box.conn, run_id)
        run.state = RunState.COMPLETE
        runs_repo.save_run(box.conn, run)
        original_acquire(self)

    monkeypatch.setattr(RunLock, "acquire", finish_then_acquire)
    capsys.readouterr()

    assert main(["drive", run_id, "--no-preflight"], container=box) == 2

    assert runs_repo.get_run(box.conn, run_id).state == RunState.COMPLETE
    assert not any(call[0] == "worktree_add" for call in box.git.calls), "the stale copy was never driven"


def test_drive_refuses_while_a_coordinator_holds_the_lock(settings, capsys):
    from review_loop.services.locks import RunLock

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    lock = RunLock(settings.state_dir, "webapp", 1004)
    lock.acquire()
    try:
        assert main(["drive", run_id, "--no-preflight"], container=box) == 3
    finally:
        lock.release()

    assert runs_repo.get_run(box.conn, run_id).state == RunState.PREPARING


# --- drive probes too, unless its caller already did ------------------------------------------------------

def test_drive_probes_the_agents_before_its_first_phase(settings, capsys):
    from tests.services.test_preflight import agents

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    box.agents = agents(author_ok=False)
    box.process.script(["git", "init"])
    capsys.readouterr()

    assert main(["drive", run_id], container=box) == 1

    run = runs_repo.get_run(box.conn, run_id)
    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.AGENT_UNAVAILABLE
    assert not any(call[0] == "worktree_add" for call in box.git.calls), "no phase ran"


def test_a_detached_coordinator_is_told_its_parent_already_probed(settings):
    box = detachable(settings)

    main(["start", URL, "--detach"], container=box)

    assert box.spawn.calls[0]["argv"][-3:] == ["drive", runs_repo.list_runs(box.conn)[0].id, "--no-preflight"]


def test_a_stop_that_lands_during_a_crashing_drive_is_not_undone_by_the_crash_pause(settings, capsys):
    from review_loop.services import run_control

    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id

    def stop_then_explode(ref):
        run_control.stop(box.conn, runs_repo.get_run(box.conn, run_id), box.clock)
        raise RuntimeError("boom")

    box.github.fetch_pull = stop_then_explode
    capsys.readouterr()

    assert main(["drive", run_id, "--no-preflight"], container=box) == 1

    run = runs_repo.get_run(box.conn, run_id)
    assert run.state == RunState.CANCELLED and run.pause_reason is None
    assert f"run {run_id}: cancelled" in capsys.readouterr().out
