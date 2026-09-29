from __future__ import annotations

import json
import sqlite3
from typing import Any


def record_intent(conn: sqlite3.Connection, run_id: str, kind: str, payload: dict[str, Any], marker: str, now: str) -> int:
    cursor = conn.execute(
        "insert into outbox (run_id, kind, payload_json, marker, state, created_at) values (?, ?, ?, ?, 'intended', ?)",
        (run_id, kind, json.dumps(payload), marker, now),
    )
    return int(cursor.lastrowid)


def reserve_unless_controlled(conn: sqlite3.Connection, run_id: str, kind: str, payload: dict[str, Any], marker: str, now: str) -> int | None:
    """Record intent only while no person has cancelled the run or paused it by hand, in one statement. The reservation
    is the point past which a control lands too late: the effect then counts as in flight and finishes."""
    cursor = conn.execute(
        "insert into outbox (run_id, kind, payload_json, marker, state, created_at) select ?, ?, ?, ?, 'intended', ? from runs "
        "where id = ? and state != 'cancelled' and not (state = 'paused' and pause_reason = 'manual')",
        (run_id, kind, json.dumps(payload), marker, now, run_id),
    )
    return int(cursor.lastrowid) if cursor.rowcount == 1 else None


def mark_done(conn: sqlite3.Connection, entry_id: int, receipt: dict[str, Any], now: str) -> None:
    conn.execute("update outbox set state = 'done', receipt_json = ?, done_at = ? where id = ?", (json.dumps(receipt), now, entry_id))


def mark_uncertain(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("update outbox set state = 'uncertain' where id = ?", (entry_id,))


def mark_unsent(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("update outbox set state = 'unsent' where id = ?", (entry_id,))


def list_entries(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("select id, kind, payload_json, marker, state, receipt_json, created_at, done_at from outbox "
                        "where run_id = ? order by id", (run_id,)).fetchall()
    return [_entry(row) for row in rows]


def unsettled_entries(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return [entry for entry in list_entries(conn, run_id) if entry["state"] in ("intended", "uncertain")]


def _entry(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "kind": row["kind"], "payload": json.loads(row["payload_json"]), "marker": row["marker"],
            "state": row["state"], "receipt": json.loads(row["receipt_json"]), "created_at": row["created_at"], "done_at": row["done_at"]}


def has_done(conn: sqlite3.Connection, run_id: str, marker: str) -> bool:
    row = conn.execute("select 1 from outbox where run_id = ? and marker = ? and state = 'done' limit 1", (run_id, marker)).fetchone()
    return row is not None


def find_done(conn: sqlite3.Connection, run_id: str, kind: str) -> dict[str, Any] | None:
    for entry in reversed(list_entries(conn, run_id)):
        if entry["kind"] == kind and entry["state"] == "done":
            return entry
    return None
