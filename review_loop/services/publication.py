"""Every external write goes through the outbox: intent first, the call, then the receipt."""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from review_loop.repositories import outbox as outbox_repo
from review_loop.repositories import runs as runs_repo
from review_loop.types.protocols import Clock, GitHubGateway
from review_loop.types.pull_request import PullRef
from review_loop.types.run import Run


class ControlLanded(Exception):
    """A person stopped or paused the run after the phase's own check: the effect was withheld and nothing was sent.
    `run` is the run as it stands, for the phase to return."""

    def __init__(self, run: Run):
        super().__init__(f"run {run.id} is {run.state.value}; the effect was withheld")
        self.run = run


class Publisher:
    def __init__(self, conn: sqlite3.Connection, github: GitHubGateway, clock: Clock, forbidden: list[str] | None = None):
        self.conn = conn
        self.github = github
        self.clock = clock
        self.forbidden = list(forbidden or [])

    def post(self, run_id: str, kind: str, payload: dict[str, Any], marker: str, body: str,
             send: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
        """A body-carrying effect: the body is cleaned of provenance lines right before it is sent."""
        from review_loop.engine.trailers import clean_for_github

        cleaned = clean_for_github(body, self.forbidden)
        return self.perform(run_id, kind, payload, marker, lambda: send(cleaned))

    def perform(self, run_id: str, kind: str, payload: dict[str, Any], marker: str,
                operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Intent is reserved in one statement that fails once a person has stopped the run or paused it by hand, so
        nothing starts after their control; an effect already reserved is in flight and finishes."""
        entry_id = outbox_repo.reserve_unless_controlled(self.conn, run_id, kind, payload, marker, self._now())
        if entry_id is None:
            current = runs_repo.get_run(self.conn, run_id)
            if current is None:
                raise RuntimeError(f"run {run_id} is not recorded")
            raise ControlLanded(current)
        try:
            receipt = operation()
        except Exception:
            outbox_repo.mark_uncertain(self.conn, entry_id)
            raise
        outbox_repo.mark_done(self.conn, entry_id, receipt, self._now())
        return receipt

    def reconcile(self, run_id: str, ref: PullRef) -> list[tuple[int, str]]:
        """Settle every entry whose outcome is unknown by looking for its marker on the PR; never re-send here."""
        outcomes = []
        for entry in outbox_repo.unsettled_entries(self.conn, run_id):
            found = self.github.find_post_by_marker(ref, entry["marker"]) if entry["marker"] else None
            if found:
                outbox_repo.mark_done(self.conn, entry["id"], {**found, "reconciled": True}, self._now())
                outcomes.append((entry["id"], "done"))
            else:
                outbox_repo.mark_unsent(self.conn, entry["id"])
                outcomes.append((entry["id"], "unsent"))
        return outcomes

    def _now(self) -> str:
        return self.clock.now().isoformat()
