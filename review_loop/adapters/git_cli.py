"""The git binary behind the GitClient protocol."""

from __future__ import annotations

from pathlib import Path

from review_loop.types.protocols import ProcessRunner
from review_loop.types.result import Err, Ok, Result


class GitCli:
    def __init__(self, process: ProcessRunner, env: dict[str, str], timeout_seconds: int = 600):
        self.process = process
        self.env = env
        self.timeout_seconds = timeout_seconds

    def merge_base(self, repo_path: str, one: str, two: str) -> str:
        return self._git(repo_path, "merge-base", one, two)

    def fetch(self, repo_path: str, remote: str, refs: list[str]) -> None:
        self._git(repo_path, "fetch", "--quiet", "--", remote, *refs)

    def worktree_exists(self, path: str) -> bool:
        return (Path(path) / ".git").exists()

    def worktree_add(self, repo_path: str, path: str, branch: str, start_point: str) -> None:
        self._git(repo_path, "worktree", "add", "-B", branch, path, start_point)

    def is_clean(self, path: str) -> bool:
        return self._git(path, "status", "--porcelain") == ""

    def reset_hard(self, path: str, sha: str) -> None:
        self._git(path, "reset", "--hard", "--quiet", sha)

    def set_worktree_push_url(self, path: str, remote: str, url: str) -> None:
        """Make `url` the only push URL of `remote` in this worktree. It goes in the worktree's own config file, since every checkout reads the shared one."""
        if self._git(path, "config", "--local", "--type=bool", "--default=false", "--get", "extensions.worktreeConfig") != "true":
            raise RuntimeError(f"cannot set the push URL of {remote} for {path} alone: its repository has extensions.worktreeConfig off. "
                               f"Turn it on once with `git -C {path} config extensions.worktreeConfig true`")
        key = f"remote.{remote}.pushurl"
        # Push URLs add up across config files; the empty value drops any the shared config sets.
        self._git(path, "config", "--worktree", "--replace-all", key, "")
        self._git(path, "config", "--worktree", "--add", key, url)
        others = set(self._git(path, "remote", "get-url", "--push", "--all", remote).splitlines()) - {"", url}
        if others:
            raise RuntimeError(f"{remote} in {path} would still push to {', '.join(sorted(others))}; "
                               "clearing push URLs inherited from the shared config needs git 2.46 or newer")

    def changed_files(self, repo_path: str, base: str, head: str) -> list[str]:
        return _names(self._git_raw(repo_path, "diff", "--name-only", "-z", f"{base}..{head}"))

    def diff(self, repo_path: str, base: str, head: str) -> str:
        return self._git(repo_path, "diff", f"{base}..{head}") + "\n"

    def log_between(self, repo_path: str, base: str, head: str) -> list[str]:
        lines = self._git(repo_path, "log", "--format=%H %s", f"{base}..{head}").splitlines()
        return [f"{line[:9]} {line[41:]}" for line in lines]

    def head_sha(self, path: str) -> str:
        return self._git(path, "rev-parse", "HEAD")

    def current_branch(self, path: str) -> str:
        return self._git(path, "rev-parse", "--abbrev-ref", "HEAD")

    def commit_tree(self, path: str, sha: str) -> str:
        return self._git(path, "rev-parse", f"{sha}^{{tree}}")

    def file_hash(self, path: str, sha: str, file: str) -> str:
        completed = self.process.run(["git", "rev-parse", f"{sha}:{file}"], cwd=path, env=self.env, timeout_seconds=self.timeout_seconds)
        return completed.stdout.strip() if completed.exit_code == 0 else ""

    def paths_differing_from(self, path: str, base: str, pathspecs: list[str]) -> list[str]:
        """Tracked paths whose working-tree content differs from base, and every untracked path, ignored ones included,
        limited to pathspecs."""
        tracked = self._git_raw(path, "diff", "--name-only", "-z", base, "--", *pathspecs)
        untracked = self._git_raw(path, "ls-files", "--others", "-z", "--", *pathspecs)
        return _names(tracked + untracked)

    def working_changed_files(self, path: str) -> list[str]:
        """Tracked paths that differ from HEAD, staged or not, both sides of a move included, and untracked paths git
        does not ignore."""
        tracked = self._git_raw(path, "diff", "--name-only", "-z", "--no-renames", "HEAD", "--")
        untracked = self._git_raw(path, "ls-files", "--others", "--exclude-standard", "-z", "--")
        return _names(tracked + untracked)

    def stage_all_and_tree_hash(self, path: str) -> str:
        self._git(path, "add", "-A")
        return self._git(path, "write-tree")

    def commit(self, path: str, message: str) -> str:
        """No hooks at all: --no-verify leaves prepare-commit-msg running, and a hook could append a trailer."""
        self._git(path, "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "--no-verify", "-m", message)
        return self.head_sha(path)

    def commit_info(self, path: str, sha: str) -> tuple[str, str]:
        parents = self._git(path, "rev-list", "--parents", "-n", "1", sha).split()[1:]
        message = self._git(path, "log", "-1", "--format=%B", sha)
        return (parents[0] if parents else "", message)

    def is_ancestor(self, path: str, ancestor: str, descendant: str) -> bool:
        completed = self.process.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=path, env=self.env,
                                     timeout_seconds=self.timeout_seconds)
        return completed.exit_code == 0

    def push_guarded(self, path: str, url: str, sha: str, remote_branch: str, expected_remote_sha: str) -> Result[str, str]:
        """Push one verified commit to one branch: the remote must still show the expected parent, the push
        is by SHA with a lease and no implicit refs, and the remote must show the SHA afterwards."""
        remote_sha = self._remote_sha(path, url, remote_branch)
        if remote_sha == sha:
            return Ok(sha)
        if remote_sha != expected_remote_sha:
            return Err("head_changed")
        if remote_sha and not self.is_ancestor(path, remote_sha, sha):
            return Err("not_fast_forward")
        completed = self.process.run(["git", "push", "--quiet", "--no-follow-tags", "--recurse-submodules=no", url,
                                      f"{sha}:refs/heads/{remote_branch}", f"--force-with-lease=refs/heads/{remote_branch}:{expected_remote_sha}"],
                                     cwd=path, env=self.env, timeout_seconds=self.timeout_seconds)
        if completed.exit_code != 0:
            if "stale info" in completed.stderr or "rejected" in completed.stderr:
                return Err("head_changed")
            raise RuntimeError(f"git push to {remote_branch} failed (exit {completed.exit_code}): {completed.stderr.strip()}")
        if self._remote_sha(path, url, remote_branch) != sha:
            return Err("push_unverified")
        return Ok(sha)

    def _remote_sha(self, path: str, url: str, branch: str) -> str:
        listed = self._git(path, "ls-remote", url, f"refs/heads/{branch}").split()
        return listed[0] if listed else ""

    def _git(self, cwd: str, *args: str) -> str:
        return self._git_raw(cwd, *args).strip()

    def _git_raw(self, cwd: str, *args: str) -> str:
        completed = self.process.run(["git", *args], cwd=cwd, env=self.env, timeout_seconds=self.timeout_seconds)
        if completed.exit_code != 0:
            raise RuntimeError(f"git {' '.join(args)} in {cwd} failed (exit {completed.exit_code}): {completed.stderr.strip()}")
        return completed.stdout


def _names(output: str) -> list[str]:
    """Paths from a -z listing, read unstripped: line output quotes unusual names, and stripping trims whitespace a
    name may start or end with."""
    return [name for name in output.split("\0") if name]
