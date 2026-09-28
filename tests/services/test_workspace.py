import os
import subprocess
from dataclasses import replace
from pathlib import Path

from review_loop.adapters.git_cli import GitCli
from review_loop.adapters.process import SubprocessRunner
from review_loop.services.workspace import WorkspaceProblem, prepare_workspace
from review_loop.types.run import Budgets, Run, RunState
from tests.fakes.git import FakeGit
from tests.fakes.process import FakeProcessRunner


def run_for(pr=1004):
    return Run(id=f"webapp-{pr}-x", repo="webapp", pr_number=pr, pr_url="u", pr_author="vinlim", head_ref="claude/x",
               base_ref="main", head_sha="h" * 40, base_sha="b" * 40, merge_base_sha="m" * 40, state=RunState.PREPARING,
               budgets=Budgets(7, 2, 1), versions={})


def make(settings, tmp_path):
    log = []
    git, process = FakeGit(log), FakeProcessRunner(log)
    process.script(["bash", ".claude/worktree-setup.sh"])
    repo = settings.repositories["webapp"]
    return log, git, process, repo


def sh(cwd, *argv):
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def real_checkout(local: Path, origin: Path) -> str:
    """A registered checkout: `main` and the PR head on a bare origin, per-worktree config on. Returns the head sha."""
    sh(local.parent, "git", "init", "--bare", "-q", "-b", "main", str(origin))
    sh(local, "git", "init", "-q", "-b", "main")
    sh(local, "git", "config", "user.email", "t@example.com")
    sh(local, "git", "config", "user.name", "t")
    sh(local, "git", "config", "extensions.worktreeConfig", "true")
    sh(local, "git", "commit", "-q", "--allow-empty", "-m", "base")
    sh(local, "git", "remote", "add", "origin", str(origin))
    sh(local, "git", "push", "-q", "origin", "main", "main:claude/x")
    return sh(local, "git", "rev-parse", "HEAD")


def test_prepare_fetches_adds_the_worktree_then_runs_the_prepare_command_in_a_guarded_environment(settings, tmp_path):
    log, git, process, repo = make(settings, tmp_path)
    base_env = {"PATH": "/usr/bin", "GH_TOKEN": "secret", "DB_URL": "pgsql://x", "DB_HOST": "h", "HOME": "/h", "CLAUDE_CODE_OAUTH_TOKEN": "t"}

    result = prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state",
                               base_env=base_env, changed_paths=["app/Foo.php"])

    assert result.ok
    expected_path = str(repo.worktree_root / "webapp-1004")
    assert log == [("git", "fetch"), ("git", "worktree_add"), ("git", "set_worktree_push_url"), ("process", ["bash", ".claude/worktree-setup.sh"])]
    assert git.calls[0] == ("fetch", str(repo.local_path), "origin", ["main", "claude/x"])
    assert git.calls[1] == ("worktree_add", str(repo.local_path), expected_path, "review-loop/pr-1004", "h" * 40)
    assert git.calls[2] == ("set_worktree_push_url", expected_path, "origin", "DISABLED")
    prepare_call = process.calls[0]
    assert prepare_call["cwd"] == expected_path
    env = prepare_call["env"]
    assert env["PATH"].startswith(str(tmp_path / "state" / "shims") + ":")
    assert "GH_TOKEN" not in env and "DB_URL" not in env and "DB_HOST" not in env and env["HOME"] == "/h"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["GIT_CONFIG_KEY_0"] == "remote.origin.pushurl" and env["GIT_CONFIG_VALUE_0"] == "DISABLED"
    assert (tmp_path / "state" / "shims" / "gh").exists()
    assert result.value.path == expected_path and result.value.local_branch == "review-loop/pr-1004"


def test_prepare_refuses_pushes_from_its_worktree_and_leaves_the_main_checkout_push_url_alone(settings, tmp_path):
    repo = settings.repositories["webapp"]
    head = real_checkout(repo.local_path, tmp_path / "origin.git")
    main_push_url = sh(repo.local_path, "git", "remote", "get-url", "--push", "--all", "origin")
    process = FakeProcessRunner()
    process.script(["bash", ".claude/worktree-setup.sh"])

    result = prepare_workspace(replace(run_for(), head_sha=head), repo, git=GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]}),
                               process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"}, changed_paths=[])

    assert result.ok
    assert sh(repo.local_path, "git", "remote", "get-url", "--push", "--all", "origin") == main_push_url
    push = subprocess.run(["git", "push", "-q", "origin", "HEAD:refs/heads/leak"], cwd=result.value.path, capture_output=True, text=True)
    assert push.returncode != 0
    assert sh(repo.local_path, "git", "ls-remote", "origin", "refs/heads/leak") == ""


def test_prepare_runs_the_extra_command_only_when_a_changed_path_matches(settings, tmp_path):
    log, git, process, repo = make(settings, tmp_path)
    process.script(["bash", ".claude/worktree-setup.sh", "--js"])

    prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                      changed_paths=["resources/js/app.ts"])
    js_calls = [call for call in process.calls if call["argv"][-1] == "--js"]
    assert len(js_calls) == 1

    log.clear(); process.calls.clear()
    prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                      changed_paths=["app/Foo.php"])
    assert not [call for call in process.calls if call["argv"][-1] == "--js"]


def test_an_existing_worktree_is_reset_to_the_head_when_clean(settings, tmp_path):
    log, git, process, repo = make(settings, tmp_path)
    path = str(repo.worktree_root / "webapp-1004")
    git.worktrees.add(path)
    git.branches[path] = "review-loop/pr-1004"

    result = prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                               changed_paths=[])

    assert result.ok
    assert ("reset_hard", path, "h" * 40) in git.calls
    assert not any(call[0] == "worktree_add" for call in git.calls)


def test_a_dirty_worktree_pauses_with_workspace_dirty_and_touches_nothing(settings, tmp_path):
    log, git, process, repo = make(settings, tmp_path)
    path = str(repo.worktree_root / "webapp-1004")
    git.worktrees.add(path)
    git.branches[path] = "review-loop/pr-1004"
    git.clean[path] = False

    result = prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                               changed_paths=[])

    assert not result.ok and result.error == WorkspaceProblem.DIRTY
    assert not any(call[0] in ("reset_hard", "worktree_add") for call in git.calls)
    assert process.calls == []


def test_a_failing_prepare_command_is_reported_not_ignored(settings, tmp_path):
    log, git, process, repo = make(settings, tmp_path)
    process.scripts.clear()
    process.script(["bash", ".claude/worktree-setup.sh"], exit_code=1, stderr="composer: not found")

    result = prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                               changed_paths=[])

    assert not result.ok and result.error == WorkspaceProblem.PREPARE_FAILED


def test_a_failing_prepare_writes_a_log_with_the_command_its_exit_code_and_output_then_runs_nothing_else(settings, tmp_path):
    log, git, process, repo = make(settings, tmp_path)
    process.scripts.clear()
    process.script(["bash", ".claude/worktree-setup.sh"], exit_code=1, stdout="composer install", stderr="composer: not found")
    log_path = tmp_path / "prepare-1.log"

    result = prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                               changed_paths=["resources/js/app.ts"], log_path=log_path)

    assert not result.ok and result.error == WorkspaceProblem.PREPARE_FAILED
    assert log_path.read_text() == "$ bash .claude/worktree-setup.sh\nexit 1\ncomposer install\ncomposer: not found"
    assert len(process.calls) == 1


def test_a_successful_prepare_writes_its_log_too_one_block_per_command(settings, tmp_path):
    log, git, process, repo = make(settings, tmp_path)
    process.scripts.clear()
    process.script(["bash", ".claude/worktree-setup.sh"], stdout="dependencies installed")
    log_path = tmp_path / "prepare-1.log"

    result = prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                               changed_paths=["resources/js/app.ts"], log_path=log_path)

    assert result.ok
    assert log_path.read_text() == ("$ bash .claude/worktree-setup.sh\nexit 0\ndependencies installed\n\n"
                                    "$ bash .claude/worktree-setup.sh --js\nexit 0\ndependencies installed\n")


def test_a_timed_out_prepare_command_fails_the_preparation_and_is_marked_in_the_log(settings, tmp_path):
    log, git, process, repo = make(settings, tmp_path)
    process.scripts.clear()
    process.script(["bash", ".claude/worktree-setup.sh"], exit_code=-1, timed_out=True, stdout="downloading vendor packages")
    log_path = tmp_path / "prepare-1.log"

    result = prepare_workspace(run_for(), repo, git=git, process=process, state_dir=tmp_path / "state", base_env={"PATH": "/usr/bin"},
                               changed_paths=[], log_path=log_path)

    assert not result.ok and result.error == WorkspaceProblem.PREPARE_FAILED
    assert log_path.read_text().startswith("$ bash .claude/worktree-setup.sh\nexit -1 (timed out)\ndownloading vendor packages")


def test_the_shims_directory_holds_the_empty_git_and_gh_configuration_the_environment_points_at(tmp_path):
    from review_loop.services.workspace import ensure_shims

    shims = ensure_shims(tmp_path / "state")

    assert (shims / "empty-gitconfig").exists() and (shims / "empty-gh-config").is_dir()
