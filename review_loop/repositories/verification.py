from __future__ import annotations

import json
import sqlite3


def add_result(conn: sqlite3.Connection, run_id: str, pass_no: int, attempt_no: int, tree_hash: str, commands: list[list[str]],
               status: str, log_path: str, now: str) -> int:
    cursor = conn.execute("insert into verification_results (run_id, pass_no, attempt_no, tree_hash, commands_json, status, log_path, created_at) "
                          "values (?, ?, ?, ?, ?, ?, ?, ?)", (run_id, pass_no, attempt_no, tree_hash, json.dumps(commands), status, log_path, now))
    return int(cursor.lastrowid)


def list_results(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute("select pass_no, attempt_no, tree_hash, commands_json, status, log_path, created_at from verification_results "
                        "where run_id = ? order by id", (run_id,)).fetchall()
    return [{"pass_no": row["pass_no"], "attempt_no": row["attempt_no"], "tree_hash": row["tree_hash"], "commands": json.loads(row["commands_json"]),
             "status": row["status"], "log_path": row["log_path"], "created_at": row["created_at"]} for row in rows]
