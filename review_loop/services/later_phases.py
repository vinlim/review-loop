"""Dispatch for the phases after assessment; alignment lands with milestone 4."""

from __future__ import annotations

from review_loop.services.fix_verify import phase_fix, phase_verify
from review_loop.services.publish import phase_publish
from review_loop.types.run import Run, RunState


def step(deps, run: Run) -> Run:
    handlers = {RunState.FIXING: phase_fix, RunState.VERIFYING: phase_verify, RunState.PUBLISHING: phase_publish}
    if run.state in handlers:
        return handlers[run.state](deps, run)
    if run.state == RunState.ALIGNING:
        from review_loop.services.alignment import phase_align

        return phase_align(deps, run)
    raise NotImplementedError(f"no phase handles state {run.state.value}")
