"""One worktree per pull request, on a tool-owned branch; pushes are disabled through the agents' environment and gh is shimmed away."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from review_loop.config.settings import RepositoryConfig
from review_loop.engine.env import sanitize_env
from review_loop.types.protocols import GitClient, ProcessRunner
from review_loop.types.result import Err, Ok, Result
from review_loop.types.run import Run

GH_SHIM = "#!/bin/sh\necho 'gh is not available to agents; the review-loop coordinator posts to GitHub' >&2\nexit 1\n"


class WorkspaceProblem(StrEnum):
    DIRTY = "dirty"
    FOREIGN = "foreign"
    UNEXPECTED_COMMIT = "unexpected_commit"
    PREPARE_FAILED = "prepare_failed"


@dataclass(frozen=True)
class WorkspaceReady:
    path: str
    local_branch: str
    env: dict[str, str]
    discarded_patch: str = ""  # where the loop's own unverified fix went, when preparation had to discard one


def workspace_path(repo: RepositoryConfig, pr_number: int) -> str:
    return str(repo.worktree_root / f"{repo.name}-{pr_number}")


def local_branch(pr_number: int) -> str:
    return f"review-loop/pr-{pr_number}"


def prepare_workspace(run: Run, repo: RepositoryConfig, *, git: GitClient, process: ProcessRunner, state_dir: Path,
                      base_env: dict[str, str], changed_paths: list[str], timeout_seconds: int = 1800,
                      log_path: Path | None = None, loop_tree: str = "", discarded_patch_path: Path | None = None,
                      on_discard: Callable[[str], None] | None = None) -> Result[WorkspaceReady, WorkspaceProblem]:
    """`loop_tree` is the tree the loop last left in the worktree. A dirty worktree holding exactly that tree is the loop's
    own unverified fix: it is kept as a patch at `discarded_patch_path` and discarded. Any other dirty tree pauses, and
    so does a clean worktree at another commit whose tree is not that one: someone committed there.
    `on_discard` records the patch before the worktree is reset or any prepare command runs, so a failure after it
    cannot lose the receipt."""
    path = workspace_path(repo, run.pr_number)
    branch = local_branch(run.pr_number)
    discarded = ""
    git.fetch(str(repo.local_path), "origin", [run.base_ref, run.head_ref])
    if git.worktree_exists(path):
        if git.current_branch(path) != branch:
            return Err(WorkspaceProblem.FOREIGN)
        if not git.is_clean(path):
            if not loop_tree or discarded_patch_path is None or git.working_tree_hash(path) != loop_tree:
                return Err(WorkspaceProblem.DIRTY)
            discarded_patch_path.parent.mkdir(parents=True, exist_ok=True)
            git.write_patch(path, git.head_sha(path), loop_tree, str(discarded_patch_path))
            if on_discard is not None:
                on_discard(str(discarded_patch_path))
            git.stage_all_and_tree_hash(path)  # staged, the fix's new files are removed by the reset as well
            discarded = str(discarded_patch_path)
        elif loop_tree and git.head_sha(path) != run.head_sha and git.working_tree_hash(path) != loop_tree:
            return Err(WorkspaceProblem.UNEXPECTED_COMMIT)
        git.reset_hard(path, run.head_sha)
    else:
        git.worktree_add(str(repo.local_path), path, branch, run.head_sha)
    git.set_worktree_push_url(path, "origin", "DISABLED")
    env = sanitize_env(base_env, repo.workspace.sanitize_env, str(ensure_shims(state_dir)))
    prepared, log = _run_prepare(prepare_commands(repo, changed_paths), process, path, env, timeout_seconds)
    if log_path is not None:
        log_path.write_text(log)
    if not prepared:
        return Err(WorkspaceProblem.PREPARE_FAILED)
    return Ok(WorkspaceReady(path=path, local_branch=branch, env=env, discarded_patch=discarded))


def _run_prepare(commands: list[list[str]], process: ProcessRunner, cwd: str, env: dict[str, str], timeout_seconds: int) -> tuple[bool, str]:
    """The first failure ends the sequence; the log keeps one block per command that ran, in the shape verification logs use,
    so an operator can read a paused run without rerunning the setup by hand."""
    blocks: list[str] = []
    for command in commands:
        completed = process.run(command, cwd=cwd, env=env, timeout_seconds=timeout_seconds)
        blocks.append(f"$ {' '.join(completed.argv)}\nexit {completed.exit_code}{' (timed out)' if completed.timed_out else ''}\n{completed.stdout}\n{completed.stderr}")
        if completed.exit_code != 0 or completed.timed_out:
            return False, "\n".join(blocks)
    return True, "\n".join(blocks)


def prepare_commands(repo: RepositoryConfig, changed_paths: list[str]) -> list[list[str]]:
    commands = list(repo.workspace.prepare)
    for pattern, extra in repo.workspace.prepare_when_paths_match.items():
        if any(re.search(pattern, changed) for changed in changed_paths):
            commands.extend(extra)
    return commands


def ensure_shims(state_dir: Path) -> Path:
    shims = Path(state_dir) / "shims"
    shims.mkdir(parents=True, exist_ok=True)
    shim = shims / "gh"
    if not shim.exists() or shim.read_text() != GH_SHIM:
        shim.write_text(GH_SHIM)
        shim.chmod(0o755)
    (shims / "empty-gitconfig").touch()
    (shims / "empty-gh-config").mkdir(exist_ok=True)
    return shims


def registered_script_files(repo: RepositoryConfig) -> set[str]:
    """Files the registered commands execute from the checkout; a PR or a fix that changes them is not trusted."""
    commands = (list(repo.workspace.prepare) + list(repo.verification.required) + list(repo.verification.fallback)
                + list(repo.verification.format))
    for extra in repo.workspace.prepare_when_paths_match.values():
        commands.extend(extra)
    return {argument for command in commands for argument in command[:2] if "/" in argument}
