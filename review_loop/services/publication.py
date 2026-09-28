"""Every external write goes through the outbox: intent first, the call, then the receipt."""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from review_loop.repositories import outbox as outbox_repo
from review_loop.types.protocols import Clock, GitHubGateway
from review_loop.types.pull_request import PullRef


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
        entry_id = outbox_repo.record_intent(self.conn, run_id, kind, payload, marker, self._now())
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
