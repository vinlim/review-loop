from __future__ import annotations

import json
import sqlite3

_FIELDS = ("title", "description", "file", "symbol", "evidence", "impact", "next_step", "source_pr", "source_commit", "source_run", "agent")


def add_item(conn: sqlite3.Connection, repo: str, item: dict, now: str) -> int:
    cursor = conn.execute(
        f"insert into inbox_items (repo, {', '.join(_FIELDS)}, status, related_json, created_at, updated_at) "
        f"values (?, {', '.join('?' for _ in _FIELDS)}, 'new', '[]', ?, ?)",
        (repo, *[item.get(field, "") for field in _FIELDS], now, now),
    )
    return int(cursor.lastrowid)


def list_items(conn: sqlite3.Connection, repo: str | None = None, status: str | None = None) -> list[dict]:
    query = "select * from inbox_items where 1 = 1"
    params: list = []
    if repo is not None:
        query += " and repo = ?"
        params.append(repo)
    if status is not None:
        query += " and status = ?"
        params.append(status)
    rows = conn.execute(query + " order by id", params).fetchall()
    return [_item(row) for row in rows]


def get_item(conn: sqlite3.Connection, item_id: int) -> dict | None:
    row = conn.execute("select * from inbox_items where id = ?", (item_id,)).fetchone()
    return _item(row) if row else None


def set_status(conn: sqlite3.Connection, item_id: int, status: str, reference: str, now: str) -> None:
    conn.execute("update inbox_items set status = ?, reference = ?, updated_at = ? where id = ?", (status, reference, now, item_id))


def add_related(conn: sqlite3.Connection, item_id: int, related_id: int, now: str) -> None:
    item = get_item(conn, item_id)
    related = sorted(set(item["related"]) | {related_id})
    conn.execute("update inbox_items set related_json = ?, updated_at = ? where id = ?", (json.dumps(related), now, item_id))


def _item(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["related"] = json.loads(item.pop("related_json"))
    return item


def add_evidence(conn: sqlite3.Connection, item_id: int, evidence: str, now: str) -> None:
    item = get_item(conn, item_id)
    combined = (item["evidence"] + "\n" + evidence).strip()
    conn.execute("update inbox_items set evidence = ?, updated_at = ? where id = ?", (combined, now, item_id))
