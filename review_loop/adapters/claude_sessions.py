"""Find the desktop session that authored a branch: session files record gitBranch on each line."""

from __future__ import annotations

import json
from pathlib import Path


def find_author_session(projects_dir: Path, head_branch: str, local_path: str = "") -> str:
    best: tuple[float, str] | None = None
    for path in Path(projects_dir).glob("*/*.jsonl"):
        if not _mentions_branch(path, head_branch, local_path):
            continue
        mtime = path.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, path.stem)
    return best[1] if best else ""


def _mentions_branch(path: Path, head_branch: str, local_path: str) -> bool:
    """Only lines that name a gitBranch are parsed; transcripts run to tens of thousands of lines.
    With a local path, the line must also have worked inside that checkout or one of its worktrees."""
    try:
        with path.open() as handle:
            for line in handle:
                if '"gitBranch"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("gitBranch") != head_branch:
                    continue
                if not local_path or str(record.get("cwd", "")).startswith(local_path.rstrip("/")):
                    return True
    except OSError:
        return False
    return False
