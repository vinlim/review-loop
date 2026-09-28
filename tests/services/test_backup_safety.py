import os
import sqlite3
import subprocess
import tarfile

from review_loop.repositories.db import connect, migrate
from review_loop.services.backup import backup_state


def test_the_database_is_snapshotted_with_the_backup_api_while_a_connection_is_open(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    conn = connect(str(state / "state.db"))
    migrate(conn)
    conn.execute("insert into inbox_items (repo, title, source_pr, created_at, updated_at) values ('r', 'open item', 1, 't', 't')")

    archive = backup_state(state, tmp_path / "backups", conn=conn)

    with tarfile.open(archive) as tar:
        tar.extract("state.db", tmp_path / "out", filter="data")
    copy = sqlite3.connect(str(tmp_path / "out" / "state.db"))
    assert copy.execute("select count(*) from inbox_items").fetchone()[0] == 1
    assert copy.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_uncommitted_work_in_a_worktree_travels_as_a_patch(tmp_path):
    state = tmp_path / "state"
    worktree = state / "worktrees" / "webapp-7"
    worktree.mkdir(parents=True)
    env = {"PATH": os.environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=worktree, check=True, env=env)
    (worktree / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "a.txt"], cwd=worktree, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=worktree, check=True, env=env)
    (worktree / "a.txt").write_text("two\n")
    (worktree / "new.txt").write_text("new\n")

    archive = backup_state(state, tmp_path / "backups")

    with tarfile.open(archive) as tar:
        names = tar.getnames()
        patch = tar.extractfile("worktree-patches/webapp-7.patch").read().decode()
    assert not any(name.startswith("worktrees/") for name in names)
    assert "+two" in patch and "new.txt" in patch
