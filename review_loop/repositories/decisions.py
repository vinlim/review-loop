from __future__ import annotations

import json
import sqlite3


def add_decision(conn: sqlite3.Connection, run_id: str, finding_ids: list[str], source: str, note: str, decision: str,
                 rationale: dict, now: str) -> int:
    version = int(conn.execute("select coalesce(max(version), 0) + 1 from alignment_decisions where run_id = ?", (run_id,)).fetchone()[0])
    conn.execute("insert into alignment_decisions (run_id, version, finding_ids_json, source, note, decision, rationale_json, created_at) "
                 "values (?, ?, ?, ?, ?, ?, ?, ?)", (run_id, version, json.dumps(finding_ids), source, note, decision, json.dumps(rationale), now))
    return version


def list_decisions(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute("select version, finding_ids_json, source, note, decision, rationale_json, created_at from alignment_decisions "
                        "where run_id = ? order by version", (run_id,)).fetchall()
    return [{"version": row["version"], "finding_ids": json.loads(row["finding_ids_json"]), "source": row["source"], "note": row["note"],
             "decision": row["decision"], "rationale": json.loads(row["rationale_json"]), "created_at": row["created_at"]} for row in rows]
