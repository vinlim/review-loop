from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from review_loop.types.run import Budgets, Outcome, PauseReason, Run, RunState

ACTIVE_STATES = tuple(state.value for state in RunState if state not in (RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED))

_COLUMNS = (
    "id, repo, pr_number, pr_url, pr_author, head_ref, base_ref, head_sha, base_sha, merge_base_sha, state, "
    "pause_reason, resume_state, outcome, pass_no, budgets_json, versions_json, worktree_path, local_branch, "
    "author_session, extra_json, created_at, updated_at"
)


def create_run(conn: sqlite3.Connection, run: Run) -> None:
    conn.execute(
        f"insert into runs ({_COLUMNS}) values ({', '.join('?' for _ in _COLUMNS.split(', '))})",
        _row_values(run),
    )


def save_run(conn: sqlite3.Connection, run: Run) -> None:
    columns = _COLUMNS.split(", ")
    assignments = ", ".join(f"{column} = ?" for column in columns[1:])
    conn.execute(f"update runs set {assignments} where id = ?", (*_row_values(run)[1:], run.id))


def get_run(conn: sqlite3.Connection, run_id: str) -> Run | None:
    row = conn.execute(f"select {_COLUMNS} from runs where id = ?", (run_id,)).fetchone()
    return _from_row(row) if row else None


def find_active_run(conn: sqlite3.Connection, repo: str, pr_number: int) -> Run | None:
    placeholders = ", ".join("?" for _ in ACTIVE_STATES)
    row = conn.execute(
        f"select {_COLUMNS} from runs where repo = ? and pr_number = ? and state in ({placeholders}) order by created_at desc",
        (repo, pr_number, *ACTIVE_STATES),
    ).fetchone()
    return _from_row(row) if row else None


def list_runs(conn: sqlite3.Connection) -> list[Run]:
    rows = conn.execute(f"select {_COLUMNS} from runs order by created_at desc").fetchall()
    return [_from_row(row) for row in rows]


def _row_values(run: Run) -> tuple:
    return (
        run.id, run.repo, run.pr_number, run.pr_url, run.pr_author, run.head_ref, run.base_ref, run.head_sha,
        run.base_sha, run.merge_base_sha, run.state.value,
        run.pause_reason.value if run.pause_reason else None,
        run.resume_state.value if run.resume_state else None,
        run.outcome.value if run.outcome else None,
        run.pass_no, json.dumps(asdict(run.budgets)), json.dumps(run.versions), run.worktree_path, run.local_branch,
        run.author_session, json.dumps(run.extra), run.created_at, run.updated_at,
    )


def _from_row(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"], repo=row["repo"], pr_number=row["pr_number"], pr_url=row["pr_url"], pr_author=row["pr_author"],
        head_ref=row["head_ref"], base_ref=row["base_ref"], head_sha=row["head_sha"], base_sha=row["base_sha"],
        merge_base_sha=row["merge_base_sha"], state=RunState(row["state"]),
        budgets=Budgets(**json.loads(row["budgets_json"])), versions=json.loads(row["versions_json"]),
        worktree_path=row["worktree_path"], local_branch=row["local_branch"], author_session=row["author_session"],
        pass_no=row["pass_no"],
        pause_reason=PauseReason(row["pause_reason"]) if row["pause_reason"] else None,
        resume_state=RunState(row["resume_state"]) if row["resume_state"] else None,
        outcome=Outcome(row["outcome"]) if row["outcome"] else None,
        created_at=row["created_at"], updated_at=row["updated_at"], extra=json.loads(row["extra_json"]),
    )
