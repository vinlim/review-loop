"""One SQLite database holds every run, finding, decision, inbox item and outbox entry."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

MIGRATIONS: list[str] = [
    """
    create table runs (
      id text primary key, repo text not null, pr_number integer not null, pr_url text not null,
      pr_author text not null, head_ref text not null, base_ref text not null, head_sha text not null,
      base_sha text not null, merge_base_sha text not null, state text not null, pause_reason text,
      resume_state text, outcome text, pass_no integer not null default 0, budgets_json text not null,
      versions_json text not null, worktree_path text not null default '', local_branch text not null default '',
      author_session text not null default '', extra_json text not null default '{}',
      created_at text not null, updated_at text not null
    );
    create index runs_repo_pr on runs(repo, pr_number);
    create table phase_attempts (
      id integer primary key autoincrement, run_id text not null references runs(id), phase text not null,
      pass_no integer not null, attempt_no integer not null, started_at text not null, ended_at text,
      status text not null, input_path text not null default '', output_path text not null default '',
      session_id text not null default '', error_json text not null default '{}'
    );
    create table findings (
      run_id text not null references runs(id), id text not null, pass_no integer not null, severity text not null,
      state text not null, file text not null default '', line integer not null default 0, symbol text not null default '',
      title text not null, finding text not null default '', protected_behaviour text not null default '',
      evidence text not null default '', recommendation text not null default '', proposed_refactor text not null default '',
      fingerprint text not null, supersedes text not null default '', new_evidence text not null default '',
      possible_duplicate_of text not null default '', review_id integer, comment_id integer, thread_id text not null default '',
      created_at text not null, updated_at text not null, primary key (run_id, id)
    );
    create table finding_events (
      id integer primary key autoincrement, run_id text not null, finding_id text not null, at text not null,
      from_state text not null, to_state text not null, actor text not null, note_json text not null default '{}'
    );
    create table alignment_decisions (
      id integer primary key autoincrement, run_id text not null, version integer not null, finding_ids_json text not null,
      source text not null, note text not null, decision text not null, rationale_json text not null default '{}',
      created_at text not null
    );
    create table inbox_items (
      id integer primary key autoincrement, repo text not null, title text not null, description text not null default '',
      file text not null default '', symbol text not null default '', evidence text not null default '',
      impact text not null default '', next_step text not null default '', source_pr integer not null,
      source_commit text not null default '', source_run text not null default '', agent text not null default '',
      status text not null default 'new', related_json text not null default '[]', reference text not null default '',
      created_at text not null, updated_at text not null
    );
    create table outbox (
      id integer primary key autoincrement, run_id text not null, kind text not null, payload_json text not null,
      marker text not null default '', state text not null, receipt_json text not null default '{}',
      created_at text not null, done_at text
    );
    create table verification_results (
      id integer primary key autoincrement, run_id text not null, pass_no integer not null, attempt_no integer not null,
      tree_hash text not null, commands_json text not null, status text not null, log_path text not null default '',
      created_at text not null
    );
    """,
    "alter table findings add column blocking integer not null default 1",
    "alter table runs add column agents_json text not null default '{}'",
]


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("create table if not exists schema_version (version integer primary key, applied_at text not null)")
    applied = conn.execute("select coalesce(max(version), 0) from schema_version").fetchone()[0]
    for version, script in enumerate(MIGRATIONS[applied:], start=applied + 1):
        conn.execute("begin")
        for statement in _statements(script):
            conn.execute(statement)
        conn.execute("insert into schema_version (version, applied_at) values (?, ?)",
                     (version, datetime.now(timezone.utc).isoformat()))
        conn.execute("commit")


def _statements(script: str) -> list[str]:
    """Split a migration into statements; executescript would commit on its own and break the transaction."""
    return [part.strip() for part in script.split(";") if part.strip()]
