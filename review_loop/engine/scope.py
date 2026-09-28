"""Files a fix touched that no accepted finding or declared change names, so drift is visible."""

from __future__ import annotations

from pathlib import PurePosixPath


def scope_drift(changed_files: list[str], finding_files: list[str], declared_files: list[str]) -> list[str]:
    in_scope = {path for path in finding_files + declared_files if path}
    stems = {_stem(path) for path in in_scope}
    return [path for path in changed_files if path not in in_scope and _stem(path) not in stems]


def _stem(path: str) -> str:
    stem = PurePosixPath(path).stem.lower()
    for affix in ("test_", "_test"):
        stem = stem.removeprefix(affix).removesuffix(affix)
    return stem.removesuffix("test")
