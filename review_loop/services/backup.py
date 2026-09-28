"""State travels as one archive: a consistent database snapshot, config, run artifacts, and a patch per dirty
worktree. Worktrees themselves are rebuilt from the registered checkout, never archived."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from review_loop.services.state_dir import ensure_private_dir

EXCLUDED_TOP_LEVEL = {"worktrees", "shims", "locks", "state.db", "state.db-wal", "state.db-shm", "state.db-journal"}


def backup_state(state_dir: Path, backups_dir: Path, conn: sqlite3.Connection | None = None) -> Path:
    state_dir, backups_dir = Path(state_dir), Path(backups_dir)
    ensure_private_dir(backups_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive = backups_dir / f"review-loop-{stamp}.tar.gz"
    archive.touch(mode=0o600)
    with tempfile.TemporaryDirectory() as staging_name:
        staging = Path(staging_name)
        for entry in sorted(state_dir.iterdir()):
            if entry.name in EXCLUDED_TOP_LEVEL:
                continue
            (shutil.copytree if entry.is_dir() else shutil.copy2)(entry, staging / entry.name)
        if (state_dir / "state.db").exists():
            _snapshot_database(state_dir / "state.db", staging / "state.db", conn)
        _capture_worktree_patches(state_dir / "worktrees", staging / "worktree-patches")
        with tarfile.open(archive, "w:gz") as tar:
            for entry in sorted(staging.iterdir()):
                tar.add(entry, arcname=entry.name)
    return archive


def restore_state(archive: Path, target: Path) -> Path:
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"refusing to restore over a non-empty directory: {target}; move it aside first")
    ensure_private_dir(target)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(target, filter="data")
    return target


def _snapshot_database(source: Path, destination: Path, conn: sqlite3.Connection | None) -> None:
    """The SQLite backup API copies a consistent image even while the coordinator is writing."""
    origin = conn if conn is not None else sqlite3.connect(str(source))
    copy = sqlite3.connect(str(destination))
    try:
        origin.backup(copy)
    except sqlite3.DatabaseError:
        copy.close()
        destination.unlink(missing_ok=True)
        shutil.copy2(source, destination)  # not a database at all; archive the bytes so nothing is lost
        return
    finally:
        copy.close()
        if conn is None:
            origin.close()


def _capture_worktree_patches(worktrees: Path, destination: Path) -> None:
    if not worktrees.exists():
        return
    for worktree in sorted(worktrees.iterdir()):
        if not (worktree / ".git").exists():
            continue
        patch = _git(worktree, "diff", "HEAD")
        for untracked in _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines():
            patch += _git(worktree, "diff", "--no-index", "--", "/dev/null", untracked, ok_codes=(0, 1))
        if patch.strip():
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"{worktree.name}.patch").write_text(patch)


def _git(cwd: Path, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if completed.returncode not in ok_codes:
        raise RuntimeError(f"git {' '.join(args)} in {cwd} failed: {completed.stderr.strip()}")
    return completed.stdout
