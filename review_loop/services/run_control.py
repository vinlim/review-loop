"""Pause keeps a run resumable at the state it left; stop ends it and keeps everything on disk."""

from __future__ import annotations

import sqlite3

from review_loop.repositories import runs as runs_repo
from review_loop.types.protocols import Clock
from review_loop.types.result import Err, Ok, Result
from review_loop.types.run import PauseReason, Run, RunState


def pause(conn: sqlite3.Connection, run: Run, reason: PauseReason, clock: Clock) -> Run:
    if run.state != RunState.PAUSED:
        run.resume_state = run.state
    run.state = RunState.PAUSED
    run.pause_reason = reason
    return _save(conn, run, clock)


def resume(conn: sqlite3.Connection, run: Run, clock: Clock) -> Result[Run, str]:
    """A moved head cannot be resumed where it stopped: the run goes back through preparation to adopt it."""
    if run.state != RunState.PAUSED or run.resume_state is None:
        return Err(f"run {run.id} is {run.state.value}, not paused")
    run.state = RunState.PREPARING if run.pause_reason == PauseReason.HEAD_CHANGED else run.resume_state
    run.resume_state = None
    run.pause_reason = None
    return Ok(_save(conn, run, clock))


def stop(conn: sqlite3.Connection, run: Run, clock: Clock) -> Run:
    run.state = RunState.CANCELLED
    run.resume_state = None
    return _save(conn, run, clock)


def _save(conn: sqlite3.Connection, run: Run, clock: Clock) -> Run:
    run.updated_at = clock.now().isoformat()
    runs_repo.save_run(conn, run)
    return run
