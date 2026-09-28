from __future__ import annotations

import json
import sqlite3


def record_attempt(conn: sqlite3.Connection, run_id: str, phase: str, pass_no: int, attempt_no: int, started_at: str,
                   input_path: str = "", output_path: str = "") -> int:
    cursor = conn.execute(
        "insert into phase_attempts (run_id, phase, pass_no, attempt_no, started_at, status, input_path, output_path) "
        "values (?, ?, ?, ?, ?, 'running', ?, ?)",
        (run_id, phase, pass_no, attempt_no, started_at, input_path, output_path),
    )
    return int(cursor.lastrowid)


def finish_attempt(conn: sqlite3.Connection, attempt_id: int, status: str, ended_at: str, session_id: str = "", error: dict | None = None) -> None:
    conn.execute("update phase_attempts set status = ?, ended_at = ?, session_id = ?, error_json = ? where id = ?",
                 (status, ended_at, session_id, json.dumps(error or {}), attempt_id))


def count_attempts(conn: sqlite3.Connection, run_id: str, phase: str, pass_no: int) -> int:
    return int(conn.execute("select count(*) from phase_attempts where run_id = ? and phase = ? and pass_no = ?",
                            (run_id, phase, pass_no)).fetchone()[0])
