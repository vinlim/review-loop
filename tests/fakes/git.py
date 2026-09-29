class FakeGit:
    """A scripted git: merge bases and cleanliness come from tables; every call is recorded in order."""

    def __init__(self, log: list | None = None):
        self.merge_bases: dict[tuple[str, str], str] = {}
        self.worktrees: set[str] = set()
        self.clean: dict[str, bool] = {}
        self.changed: list[str] = ["app/X.php"]
        self.diff_text: str = "diff --git a/app/X.php b/app/X.php\n"
        self.log_lines: list[str] = []
        self.heads: dict[str, str] = {}
        self.working_changed: list[str] = []
        self.ignored: list[str] = []
        self.commits: list[tuple[str, str]] = []
        self.push_results: list = []
        self.pushes: list[tuple] = []
        self.file_hashes: dict[tuple[str, str], str] = {}
        self.commit_infos: dict[str, tuple[str, str]] = {}
        self.remote_heads: dict[str, str] = {}
        self.on_push = None
        self.branches: dict[str, str] = {}
        self.tree_hashes: list[str] = []
        self.calls: list[tuple] = []
        self.log = log if log is not None else []

    def _record(self, *call):
        self.calls.append(call)
        self.log.append(("git", call[0]))

    def merge_base(self, repo_path: str, one: str, two: str) -> str:
        self._record("merge_base", repo_path, one, two)
        return self.merge_bases.get((one, two), "m" * 40)

    def fetch(self, repo_path: str, remote: str, refs: list[str]) -> None:
        self._record("fetch", repo_path, remote, list(refs))

    def worktree_exists(self, path: str) -> bool:
        return path in self.worktrees

    def worktree_add(self, repo_path: str, path: str, branch: str, start_point: str) -> None:
        self._record("worktree_add", repo_path, path, branch, start_point)
        self.worktrees.add(path)
        self.heads[path] = start_point
        self.branches[path] = branch

    def current_branch(self, path: str) -> str:
        return self.branches.get(path, "")

    def is_clean(self, path: str) -> bool:
        return self.clean.get(path, True)

    def reset_hard(self, path: str, sha: str) -> None:
        self._record("reset_hard", path, sha)
        self.heads[path] = sha

    def set_worktree_push_url(self, path: str, remote: str, url: str) -> None:
        self._record("set_worktree_push_url", path, remote, url)

    def changed_files(self, repo_path: str, base: str, head: str) -> list[str]:
        self._record("changed_files", repo_path, base, head)
        return list(self.changed)

    def diff(self, repo_path: str, base: str, head: str) -> str:
        self._record("diff", repo_path, base, head)
        return self.diff_text

    def log_between(self, repo_path: str, base: str, head: str) -> list[str]:
        self._record("log_between", repo_path, base, head)
        return list(self.log_lines)

    def head_sha(self, path: str) -> str:
        return self.heads.get(path, "h" * 40)

    def paths_differing_from(self, path: str, base: str, pathspecs: list[str]) -> list[str]:
        self._record("paths_differing_from", path, base, list(pathspecs))
        return [entry for entry in self.changed + self.working_changed + self.ignored
                if any(entry == spec or entry.startswith(spec + "/") for spec in pathspecs)]

    def working_changed_files(self, path: str) -> list[str]:
        return list(self.working_changed)

    def stage_all_and_tree_hash(self, path: str) -> str:
        self._record("stage_all", path)
        if self.tree_hashes:
            return self.tree_hashes.pop(0)
        return "t" * 40

    def commit_tree(self, path: str, sha: str) -> str:
        return "t" * 40

    def commit(self, path: str, message: str) -> str:
        if not self.working_changed:
            raise RuntimeError("git commit in " + path + " failed (exit 1): nothing to commit, working tree clean")
        sha = f"{len(self.commits) + 1:0>7}" + "c" * 33
        parent = self.heads.get(path, "h" * 40)
        self.commits.append((path, message))
        self._record("commit", path, message)
        self.commit_infos[sha] = (parent, message)
        self.heads[path] = sha
        self.working_changed = []
        return sha

    def commit_info(self, path: str, sha: str) -> tuple[str, str]:
        return self.commit_infos.get(sha, ("", ""))

    def remote_branch_head(self, path: str, url: str, branch: str) -> str:
        return self.remote_heads.get(branch, "")

    def push_guarded(self, path: str, url: str, sha: str, remote_branch: str, expected_remote_sha: str):
        from review_loop.types.result import Ok

        if self.remote_heads.get(remote_branch) == sha:
            return Ok(sha)
        self.pushes.append((path, url, sha, remote_branch, expected_remote_sha))
        self._record("push", remote_branch)
        if self.push_results:
            return self.push_results.pop(0)
        self.remote_heads[remote_branch] = sha
        if self.on_push is not None:
            self.on_push(remote_branch, sha)
        return Ok(sha)

    def file_hash(self, path: str, sha: str, file: str) -> str:
        return self.file_hashes.get((sha, file), f"{sha[:6]}:{file}")
