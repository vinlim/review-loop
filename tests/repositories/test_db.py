import sqlite3

from review_loop.repositories.db import MIGRATIONS, connect, migrate

EXPECTED_TABLES = {
    "schema_version", "runs", "phase_attempts", "findings", "finding_events",
    "alignment_decisions", "inbox_items", "outbox", "verification_results",
}


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {row[0] for row in rows}


def test_migrations_create_the_schema_on_an_empty_database_and_are_idempotent():
    conn = connect(":memory:")

    migrate(conn)
    migrate(conn)

    assert EXPECTED_TABLES <= table_names(conn)
    version = conn.execute("select max(version) from schema_version").fetchone()[0]
    assert version == len(MIGRATIONS)
