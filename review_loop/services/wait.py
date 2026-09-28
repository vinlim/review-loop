"""Follow a run another process drives: each transition as it is persisted, until the run stops or its coordinator is gone."""

from __future__ import annotations

import sqlite3
from typing import Callable

from review_loop.repositories import runs as runs_repo
from review_loop.types.run import WORKING_STATES, Run

# A coordinator releases the lock only when it exits, but a freshly detached one needs a few seconds to take it.
UNATTENDED_POLLS = 5


def wait_for_run(conn: sqlite3.Connection, run_id: str, *, lock_held: Callable[[Run], bool], sleep: Callable[[float], None],
                 on_step: Callable[[Run], None], interval_seconds: float = 3.0) -> Run | None:
    """The run as it stands when it leaves the working states, or when nothing has held its lock for several polls in a
    row; None for an unknown run. `on_step` sees every change of state or pause reason after the first read."""
    last = None
    free_polls = 0
    while True:
        run = runs_repo.get_run(conn, run_id)
        if run is None:
            return None
        if last is not None and last != (run.state, run.pause_reason):
            on_step(run)
        last = (run.state, run.pause_reason)
        if run.state not in WORKING_STATES:
            return run
        free_polls = 0 if lock_held(run) else free_polls + 1
        if free_polls >= UNATTENDED_POLLS:
            return run
        sleep(interval_seconds)
