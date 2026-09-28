from __future__ import annotations

import json
import sqlite3

from review_loop.types.findings import Finding

_COLUMNS = ("id, pass_no, severity, state, file, line, symbol, title, finding, protected_behaviour, evidence, recommendation, "
            "proposed_refactor, fingerprint, supersedes, new_evidence, possible_duplicate_of, review_id, comment_id, thread_id, blocking")


def create_finding(conn: sqlite3.Connection, run_id: str, finding: Finding, now: str) -> None:
    conn.execute(
        f"insert into findings (run_id, {_COLUMNS}, created_at, updated_at) values (?, {', '.join('?' for _ in _COLUMNS.split(', '))}, ?, ?)",
        (run_id, *_values(finding), now, now),
    )


def save_finding(conn: sqlite3.Connection, run_id: str, finding: Finding, now: str) -> None:
    columns = _COLUMNS.split(", ")[1:]
    assignments = ", ".join(f"{column} = ?" for column in columns)
    conn.execute(f"update findings set {assignments}, updated_at = ? where run_id = ? and id = ?",
                 (*_values(finding)[1:], now, run_id, finding.id))


def get_finding(conn: sqlite3.Connection, run_id: str, finding_id: str) -> Finding | None:
    row = conn.execute(f"select {_COLUMNS} from findings where run_id = ? and id = ?", (run_id, finding_id)).fetchone()
    return _from_row(row) if row else None


def list_findings(conn: sqlite3.Connection, run_id: str) -> list[Finding]:
    rows = conn.execute(f"select {_COLUMNS} from findings where run_id = ? order by pass_no, id", (run_id,)).fetchall()
    return [_from_row(row) for row in rows]


def add_event(conn: sqlite3.Connection, run_id: str, finding_id: str, from_state: str, to_state: str, actor: str,
              note: dict, now: str) -> None:
    conn.execute("insert into finding_events (run_id, finding_id, at, from_state, to_state, actor, note_json) values (?, ?, ?, ?, ?, ?, ?)",
                 (run_id, finding_id, now, from_state, to_state, actor, json.dumps(note)))


def list_events(conn: sqlite3.Connection, run_id: str, finding_id: str | None = None) -> list[dict]:
    query = "select finding_id, at, from_state, to_state, actor, note_json from finding_events where run_id = ?"
    params: tuple = (run_id,)
    if finding_id is not None:
        query += " and finding_id = ?"
        params += (finding_id,)
    rows = conn.execute(query + " order by id", params).fetchall()
    return [{"finding_id": row["finding_id"], "at": row["at"], "from_state": row["from_state"], "to_state": row["to_state"],
             "actor": row["actor"], "note": json.loads(row["note_json"])} for row in rows]


def _values(finding: Finding) -> tuple:
    return (finding.id, finding.pass_no, finding.severity, finding.state, finding.file, finding.line, finding.symbol, finding.title,
            finding.finding, finding.protected_behaviour, finding.evidence, finding.recommendation, finding.proposed_refactor,
            finding.fingerprint, finding.supersedes, finding.new_evidence, finding.possible_duplicate_of, finding.review_id,
            finding.comment_id, finding.thread_id, 1 if finding.blocking else 0)


def _from_row(row: sqlite3.Row) -> Finding:
    return Finding(id=row["id"], pass_no=row["pass_no"], severity=row["severity"], state=row["state"], file=row["file"], line=row["line"],
                   symbol=row["symbol"], title=row["title"], finding=row["finding"], protected_behaviour=row["protected_behaviour"],
                   evidence=row["evidence"], recommendation=row["recommendation"], proposed_refactor=row["proposed_refactor"],
                   fingerprint=row["fingerprint"], supersedes=row["supersedes"], new_evidence=row["new_evidence"],
                   possible_duplicate_of=row["possible_duplicate_of"], review_id=row["review_id"], comment_id=row["comment_id"],
                   thread_id=row["thread_id"], blocking=bool(row["blocking"]))


def delete_pass(conn: sqlite3.Connection, run_id: str, pass_no: int) -> None:
    ids = [row[0] for row in conn.execute("select id from findings where run_id = ? and pass_no = ?", (run_id, pass_no)).fetchall()]
    for finding_id in ids:
        conn.execute("delete from finding_events where run_id = ? and finding_id = ?", (run_id, finding_id))
    conn.execute("delete from findings where run_id = ? and pass_no = ?", (run_id, pass_no))
