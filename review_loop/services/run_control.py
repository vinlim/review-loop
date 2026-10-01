"""Pause keeps a run resumable at the state it left; stop ends it and keeps everything on disk."""

from __future__ import annotations

import sqlite3

from review_loop.repositories import runs as runs_repo
from review_loop.repositories import verification as verification_repo
from review_loop.types.protocols import Clock
from review_loop.types.result import Err, Ok, Result
from review_loop.types.run import WORKING_STATES, PauseReason, Run, RunState


def pause(conn: sqlite3.Connection, run: Run, reason: PauseReason, clock: Clock) -> Run:
    """A person's pause always applies and changes only the control columns: the coordinator may have persisted progress
    since the caller read the run, and that progress is kept. A coordinator's pause writes its own copy, which is the
    latest under its lock, but never undoes a stop or a manual pause that landed meanwhile: the persisted run comes back
    instead, so the caller reports the state that stands."""
    if reason == PauseReason.MANUAL:
        result = pause_by_hand(conn, run, clock)
        return result.value if result.ok else result.error
    at = clock.now().isoformat()
    if run.state != RunState.PAUSED:
        run.resume_state = run.state
    run.state = RunState.PAUSED
    run.pause_reason = reason
    run.updated_at = at
    if runs_repo.save_run_unless_controlled(conn, run):
        return run
    return runs_repo.get_run(conn, run.id)


def resume(conn: sqlite3.Connection, run: Run, clock: Clock, unattended: bool = False, head_moved: bool | None = None) -> Result[Run, str]:
    """A run whose branch someone else moved goes back through preparation to adopt the new head, whatever it paused
    for; one whose branch did not move continues where it stopped. `head_moved` is what git showed the caller; when
    unknown, only a head_changed pause re-prepares. A run in a working state that no coordinator drives (`unattended`)
    continues from that state; every phase can be re-entered. The write lands only while the run still reads as the
    caller saw it, so a stop that came between is kept."""
    seen = (run.state, run.pause_reason, run.updated_at)
    if unattended and run.state in WORKING_STATES:
        if head_moved:
            run.state = RunState.PREPARING
    elif run.state != RunState.PAUSED or run.resume_state is None:
        return Err(f"run {run.id} is {run.state.value}, not paused")
    else:
        moved = head_moved if head_moved is not None else run.pause_reason == PauseReason.HEAD_CHANGED
        if run.pause_reason == PauseReason.CHECKS_FAILED and not run.extra.get("loop_tree"):
            _recover_loop_tree(conn, run)
        run.state = RunState.PREPARING if moved else run.resume_state
        run.resume_state = None
        run.pause_reason = None
    run.updated_at = clock.now().isoformat()
    if runs_repo.save_run_if_unchanged(conn, run, *seen):
        return Ok(run)
    current = runs_repo.get_run(conn, run.id)
    reason = f" ({current.pause_reason.value})" if current.pause_reason else ""
    return Err(f"run {run.id} changed while resuming: it is now {current.state.value}{reason}")


def _recover_loop_tree(conn: sqlite3.Connection, run: Run) -> None:
    """A checks_failed pause can carry no loop tree. The failed verification that paused it recorded the tree it checked,
    which is the tree the loop left; preparation still discards only a worktree that matches it exactly."""
    results = verification_repo.list_results(conn, run.id)
    latest = results[-1] if results else None
    if latest and latest["pass_no"] == run.pass_no and latest["status"] in ("failed", "unavailable") and latest["tree_hash"]:
        run.extra["loop_tree"] = latest["tree_hash"]


def pause_by_hand(conn: sqlite3.Connection, run: Run, clock: Clock) -> Result[Run, Run]:
    """A person's pause, through the control columns alone. A finished run is not paused: the error carries the run as it stands."""
    applied, current = runs_repo.pause_manually(conn, run.id, clock.now().isoformat())
    return Ok(current) if applied else Err(current)


def stop(conn: sqlite3.Connection, run: Run, clock: Clock) -> Result[Run, Run]:
    """Cancels through the control columns alone, so progress and history stay as the coordinator last wrote them. A
    finished run keeps its outcome: the error carries the run as it stands."""
    applied, current = runs_repo.cancel(conn, run.id, clock.now().isoformat())
    return Ok(current) if applied else Err(current)
