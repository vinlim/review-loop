"""The state directory is the confidentiality boundary: one private directory holds every artifact, the database and the locks."""

from __future__ import annotations

import stat
from pathlib import Path


def ensure_private_dir(path: Path) -> Path:
    """Created 0700; an existing directory keeps its mode, and doctor names the fix when other accounts can reach it."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def readable_by_others(path: Path) -> bool:
    """Any group or other bit lets another account list, read or traverse the directory."""
    return bool(stat.S_IMODE(path.stat().st_mode) & 0o077)
