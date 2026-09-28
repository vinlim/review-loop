"""One coordinator per repository and PR, enforced with an OS file lock so it holds across processes."""

from __future__ import annotations

import fcntl
from pathlib import Path


class AlreadyLocked(Exception):
    pass


class RunLock:
    def __init__(self, state_dir: Path, repo: str, pr_number: int):
        self.path = Path(state_dir) / "locks" / f"{repo}-{pr_number}.lock"
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise AlreadyLocked(f"another coordinator holds {self.path}") from None
        self._handle = handle

    def is_held(self) -> bool:
        """Whether some process holds this lock. A separate handle is used so the probe conflicts with any holder, this
        process included, and it is released at once; a PR that never had a coordinator gets no lock file."""
        if not self.path.exists():
            return False
        with open(self.path, "a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
