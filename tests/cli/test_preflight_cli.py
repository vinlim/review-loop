"""A run never starts a phase on an agent that cannot serve its model: start and resume probe first and pause with the answer."""

from review_loop.cli.main import main
from review_loop.repositories import runs as runs_repo
from review_loop.types.run import PauseReason, RunState
from tests.cli.test_detach import FakeSpawner
from tests.cli.test_main import URL, container
from tests.fakes.agent import FakeAgent
from tests.services.test_preflight import STALE_CLI, agents  # noqa: F401 - STALE_CLI is the shared error text


def ready(box, **which_ok):
    box.agents = agents(**which_ok)
    box.process.script(["git", "init"])


def test_a_start_whose_author_cannot_serve_its_model_pauses_before_any_phase_runs(settings, capsys):
    box = container(settings)
    ready(box, author_ok=False)

    assert main(["start", URL], container=box) == 1

    run = runs_repo.list_runs(box.conn)[0]
    assert (run.state, run.pause_reason, run.resume_state) == (RunState.PAUSED, PauseReason.AGENT_UNAVAILABLE, RunState.PREPARING)
    assert "version 2.1.280 or newer" in run.extra["preflight_failure"]
    assert not any(call[0] == "worktree_add" for call in box.git.calls)
    out = capsys.readouterr().out
    assert f"run {run.id} (preparing, publish)" in out and f"run {run.id}: paused (agent_unavailable)" in out
    main(["show", run.id], container=box)
    assert "preflight: author claude cannot serve claude-opus-5-5 at xhigh: claude exited 1" in capsys.readouterr().out


def test_a_start_with_ready_agents_goes_on_to_hand_the_run_over(settings, capsys):
    box = container(settings)
    ready(box)
    box.spawn = FakeSpawner()

    assert main(["start", URL, "--detach"], container=box) == 0

    assert len(box.spawn.calls) == 1 and runs_repo.list_runs(box.conn)[0].state == RunState.PREPARING


def test_resume_probes_again_and_keeps_the_run_paused_while_an_agent_is_still_unavailable(settings, capsys):
    box = container(settings)
    main(["start", URL, "--no-run"], container=box)
    run_id = runs_repo.list_runs(box.conn)[0].id
    main(["pause", run_id], container=box)
    ready(box, reviewer_ok=False)
    box.spawn = FakeSpawner()
    capsys.readouterr()

    assert main(["resume", run_id, "--detach"], container=box) == 1

    run = runs_repo.get_run(box.conn, run_id)
    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.AGENT_UNAVAILABLE and run.resume_state == RunState.PREPARING
    assert run.extra["preflight_failure"].startswith("reviewer codex cannot serve gpt-6.1-sol at xhigh: ")
    assert box.spawn.calls == [] and f"run {run_id}: paused (agent_unavailable)" in capsys.readouterr().out


def test_no_preflight_skips_the_probes(settings, capsys):
    box = container(settings)
    box.agents = {"codex": FakeAgent(), "claude": FakeAgent()}
    box.spawn = FakeSpawner()

    assert main(["start", URL, "--detach", "--no-preflight"], container=box) == 0

    assert len(box.spawn.calls) == 1 and all(adapter.requests == [] for adapter in box.agents.values())


def test_a_stop_that_lands_during_the_probe_is_not_undone_by_the_failed_probe(settings, capsys):
    from review_loop.services import run_control

    box = container(settings)
    ready(box, author_ok=False)

    def stop_from_another_terminal(request):
        run_control.stop(box.conn, runs_repo.list_runs(box.conn)[0], box.clock)

    box.agents["claude"].on_run = stop_from_another_terminal

    assert main(["start", URL], container=box) == 1

    run = runs_repo.list_runs(box.conn)[0]
    assert run.state == RunState.CANCELLED and run.pause_reason is None
    assert f"run {run.id}: cancelled" in capsys.readouterr().out
