import os
import subprocess
from types import SimpleNamespace

import pytest

from review_loop.adapters.git_cli import GitCli
from review_loop.adapters.process import SubprocessRunner
from review_loop.services.phase_support import read_only_breach, read_only_state
from review_loop.types.agents import PhaseRequest
from tests.fakes.process import FakeProcessRunner


def sh(cwd, *argv):
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    sh(tmp_path, "git", "init", "--bare", "-q", "-b", "main", str(origin))
    local = tmp_path / "local"
    sh(tmp_path, "git", "init", "-q", "-b", "main", str(local))
    sh(local, "git", "config", "user.email", "t@example.com")
    sh(local, "git", "config", "user.name", "t")
    sh(local, "git", "config", "extensions.worktreeConfig", "true")
    (local / "a.txt").write_text("one\n")
    sh(local, "git", "add", "a.txt")
    sh(local, "git", "commit", "-q", "-m", "one")
    sh(local, "git", "remote", "add", "origin", str(origin))
    sh(local, "git", "push", "-q", "origin", "main")
    return local


def test_the_git_adapter_prepares_a_worktree_end_to_end(repo, tmp_path):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    head = sh(repo, "git", "rev-parse", "HEAD")
    worktree = str(tmp_path / "wt")

    git.fetch(str(repo), "origin", ["main"])
    assert not git.worktree_exists(worktree)
    git.worktree_add(str(repo), worktree, "review-loop/pr-1", head)

    assert git.worktree_exists(worktree) and git.is_clean(worktree)
    assert sh(worktree, "git", "rev-parse", "--abbrev-ref", "HEAD") == "review-loop/pr-1"
    (tmp_path / "wt" / "a.txt").write_text("changed\n")
    assert not git.is_clean(worktree)
    git.reset_hard(worktree, head)
    assert git.is_clean(worktree)
    git.set_worktree_push_url(worktree, "origin", "DISABLED")
    assert sh(worktree, "git", "remote", "get-url", "--push", "origin") == "DISABLED"
    assert sh(repo, "git", "remote", "get-url", "--push", "origin") == sh(repo, "git", "remote", "get-url", "origin")
    assert git.merge_base(str(repo), head, head) == head


def test_a_push_url_in_the_shared_config_does_not_reach_through_the_worktree_lock(repo, tmp_path):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    origin = sh(repo, "git", "remote", "get-url", "origin")
    sh(repo, "git", "config", "remote.origin.pushurl", origin)
    worktree = str(tmp_path / "wt")
    git.worktree_add(str(repo), worktree, "review-loop/pr-1", sh(repo, "git", "rev-parse", "HEAD"))

    git.set_worktree_push_url(worktree, "origin", "DISABLED")

    subprocess.run(["git", "push", "-q", "origin", "HEAD:refs/heads/leak"], cwd=worktree, capture_output=True)
    assert sh(repo, "git", "ls-remote", "origin", "refs/heads/leak") == ""
    assert sh(repo, "git", "remote", "get-url", "--push", "--all", "origin") == origin


def test_the_worktree_lock_refuses_a_repository_without_per_worktree_config_and_writes_nothing(repo, tmp_path):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    sh(repo, "git", "config", "--unset", "extensions.worktreeConfig")
    worktree = str(tmp_path / "wt")
    git.worktree_add(str(repo), worktree, "review-loop/pr-1", sh(repo, "git", "rev-parse", "HEAD"))
    shared = (repo / ".git" / "config").read_text()

    with pytest.raises(RuntimeError) as raised:
        git.set_worktree_push_url(worktree, "origin", "DISABLED")

    assert f"git -C {worktree} config extensions.worktreeConfig true" in str(raised.value)
    assert (repo / ".git" / "config").read_text() == shared


def test_a_worktree_lock_that_git_reads_back_with_another_push_url_is_refused():
    process = FakeProcessRunner()
    process.script(["git", "config", "--local"], stdout="true\n")
    process.script(["git", "config", "--worktree"])
    # Git before 2.46 keeps the shared config's push URL and lists the empty value as a URL of its own.
    process.script(["git", "remote", "get-url"], stdout="https://github.com/acme/webapp.git\n\nDISABLED\n")
    git = GitCli(process, env={})

    with pytest.raises(RuntimeError) as raised:
        git.set_worktree_push_url("/wt", "origin", "DISABLED")

    assert "https://github.com/acme/webapp.git" in str(raised.value)


def test_adding_a_worktree_for_an_existing_tool_branch_resets_the_branch_instead_of_failing(repo, tmp_path):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    head = sh(repo, "git", "rev-parse", "HEAD")
    sh(repo, "git", "branch", "review-loop/pr-1", head)

    git.worktree_add(str(repo), str(tmp_path / "wt"), "review-loop/pr-1", head)

    assert git.worktree_exists(str(tmp_path / "wt"))


def test_a_failing_git_command_raises_with_the_command_and_stderr(repo):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})

    with pytest.raises(RuntimeError) as raised:
        git.reset_hard(str(repo), "0" * 40)

    assert "reset" in str(raised.value)


def test_the_git_adapter_reads_history_stages_commits_and_pushes_with_a_lease(repo, tmp_path):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
    base = sh(repo, "git", "rev-parse", "HEAD")
    (repo / "b.txt").write_text("two\n")
    sh(repo, "git", "add", "b.txt")
    sh(repo, "git", "commit", "-q", "-m", "two")
    head = sh(repo, "git", "rev-parse", "HEAD")

    assert git.changed_files(str(repo), base, head) == ["b.txt"]
    assert "+two" in git.diff(str(repo), base, head)
    assert git.log_between(str(repo), base, head) == [f"{head[:9]} two"]
    assert git.head_sha(str(repo)) == head

    (repo / "c.txt").write_text("three\n")
    assert git.working_changed_files(str(repo)) == ["c.txt"]
    tree = git.stage_all_and_tree_hash(str(repo))
    assert len(tree) == 40
    sha = git.commit(str(repo), "three\n\nBody.")
    assert git.head_sha(str(repo)) == sha and sh(repo, "git", "log", "-1", "--format=%s") == "three"

    origin = sh(repo, "git", "remote", "get-url", "origin")
    result = git.push_guarded(str(repo), origin, sha, "main", expected_remote_sha=base)
    assert result.ok and sh(repo, "git", "ls-remote", "--heads", "origin", "main").split()[0] == sha


def test_a_guarded_push_refuses_when_the_remote_moved(repo, tmp_path):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
    base = sh(repo, "git", "rev-parse", "HEAD")
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", sh(repo, "git", "remote", "get-url", "origin"), str(other))
    sh(other, "git", "config", "user.email", "o@x")
    sh(other, "git", "config", "user.name", "o")
    (other / "z.txt").write_text("z\n")
    sh(other, "git", "add", "z.txt")
    sh(other, "git", "commit", "-q", "-m", "someone else")
    sh(other, "git", "push", "-q", "origin", "main")
    (repo / "mine.txt").write_text("m\n")
    git.stage_all_and_tree_hash(str(repo))
    mine = git.commit(str(repo), "mine")

    result = git.push_guarded(str(repo), sh(repo, "git", "remote", "get-url", "origin"), mine, "main", expected_remote_sha=base)

    assert not result.ok and result.error == "head_changed"


def test_paths_differing_from_a_base_include_ignored_files_symlinks_and_names_git_would_quote(repo):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    base = sh(repo, "git", "rev-parse", "HEAD")
    (repo / ".gitignore").write_text(".opencode/cache/\n")
    (repo / ".opencode" / "plugins").mkdir(parents=True)
    (repo / ".opencode" / "plugins" / 'my "plugin".ts').write_text("export {}\n")
    sh(repo, "git", "add", ".gitignore", ".opencode")
    sh(repo, "git", "commit", "-q", "-m", "plugin")
    (repo / ".opencode" / "cache").mkdir()
    (repo / ".opencode" / "cache" / "pwn.ts").write_text("export {}\n")
    (repo / "tools").mkdir()
    os.symlink("tools", repo / ".agents")
    (repo / "a.txt").write_text("changed, outside the pathspecs\n")

    paths = git.paths_differing_from(str(repo), base, [".agents", ".opencode", "opencode.json"])

    assert sorted(paths) == [".agents", ".opencode/cache/pwn.ts", '.opencode/plugins/my "plugin".ts']


def test_file_lists_keep_an_unstaged_first_entry_whole_and_names_unquoted(repo):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    base = sh(repo, "git", "rev-parse", "HEAD")
    quoted = 'my "draft".txt'
    (repo / ".claude").mkdir()
    (repo / ".claude" / "run-tests.sh").write_text("exit 0\n")
    (repo / quoted).write_text("draft\n")
    sh(repo, "git", "add", ".claude", quoted)
    sh(repo, "git", "commit", "-q", "-m", "scripts")
    head = sh(repo, "git", "rev-parse", "HEAD")
    (repo / ".claude" / "run-tests.sh").write_text("exit 0 # edited by a fix, not staged\n")
    (repo / quoted).write_text("changed too\n")
    (repo / "new file.txt").write_text("untracked\n")

    assert sorted(git.changed_files(str(repo), base, head)) == [".claude/run-tests.sh", quoted]
    assert sorted(git.working_changed_files(str(repo))) == [".claude/run-tests.sh", quoted, "new file.txt"]


def test_the_read_only_check_sees_a_rewrite_of_an_unstaged_file_through_real_git(repo, tmp_path):
    deps = SimpleNamespace(git=GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]}))
    request = PhaseRequest(phase="review", prompt="", schema_path="", cwd=str(repo), env={}, timeout_seconds=60,
                           model="", effort="", output_dir=str(tmp_path / "pass-1" / "review"))
    (repo / "a.txt").write_text("edited before the phase\n")
    before = read_only_state(deps, request)
    (repo / "a.txt").write_text("rewritten during it\n")

    assert read_only_breach(deps, request, before) == "the worktree changed during a read-only phase: a.txt"


def test_the_git_adapter_reads_a_remote_branch_head_and_an_empty_string_for_a_missing_branch(repo, tmp_path):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    origin = str(tmp_path / "origin.git")

    assert git.remote_branch_head(str(repo), origin, "main") == sh(repo, "git", "rev-parse", "HEAD")
    assert git.remote_branch_head(str(repo), origin, "no-such-branch") == ""


def test_the_working_tree_hash_matches_staging_everything_and_leaves_the_index_alone(repo):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    head = sh(repo, "git", "rev-parse", "HEAD")
    (repo / "a.txt").write_text("two\n")
    (repo / "new.txt").write_text("brand new\n")
    before = sh(repo, "git", "status", "--porcelain")

    tree = git.working_tree_hash(str(repo))

    assert sh(repo, "git", "status", "--porcelain") == before, "the real index is untouched"
    assert tree == git.stage_all_and_tree_hash(str(repo))
    patch = git.diff(str(repo), head, tree)
    assert "+two" in patch and "+brand new" in patch
