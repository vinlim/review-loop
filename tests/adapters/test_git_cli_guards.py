"""The push and commit guards, checked on argv with a scripted runner and on a real repository."""

import json
import os
import subprocess

from review_loop.adapters.git_cli import GitCli
from review_loop.adapters.process import SubprocessRunner
from review_loop.types.protocols import CompletedRun
from tests.adapters.test_git_cli import repo, sh  # noqa: F401  (fixture)
from tests.fakes.process import FakeProcessRunner

URL = "https://github.com/acme/webapp.git"
OLD, NEW = "a" * 40, "b" * 40


def scripted(remote_before=OLD, remote_after=NEW, push_exit=0, push_stderr=""):
    process = FakeProcessRunner()
    process.script_sequence(["git", "ls-remote"], [CompletedRun([], 0, f"{remote_before}\trefs/heads/claude/x\n", ""),
                                                   CompletedRun([], 0, f"{remote_after}\trefs/heads/claude/x\n", "")])
    process.script(["git", "merge-base", "--is-ancestor"])
    process.script(["git", "push"], exit_code=push_exit, stderr=push_stderr)
    return process


def test_the_push_names_the_verified_sha_disables_implicit_refs_and_verifies_the_remote_afterwards():
    process = scripted()
    git = GitCli(process, env={"PATH": "/usr/bin"})

    result = git.push_guarded("/wt", URL, NEW, "claude/x", expected_remote_sha=OLD)

    assert result.ok and result.value == NEW
    push = next(call["argv"] for call in process.calls if call["argv"][:2] == ["git", "push"])
    assert f"{NEW}:refs/heads/claude/x" in push and URL in push
    assert "--no-follow-tags" in push and "--recurse-submodules=no" in push
    assert f"--force-with-lease=refs/heads/claude/x:{OLD}" in push
    assert not any(flag in push for flag in ("--force", "-f"))
    assert len([call for call in process.calls if call["argv"][:2] == ["git", "ls-remote"]]) == 2


def test_a_remote_that_does_not_show_the_pushed_sha_afterwards_is_reported_not_trusted():
    process = scripted(remote_after="c" * 40)
    git = GitCli(process, env={"PATH": "/usr/bin"})

    result = git.push_guarded("/wt", URL, NEW, "claude/x", expected_remote_sha=OLD)

    assert not result.ok and result.error == "push_unverified"


def test_a_remote_that_already_holds_the_candidate_needs_no_push():
    process = scripted(remote_before=NEW)
    git = GitCli(process, env={"PATH": "/usr/bin"})

    result = git.push_guarded("/wt", URL, NEW, "claude/x", expected_remote_sha=OLD)

    assert result.ok and result.value == NEW
    assert not any(call["argv"][:2] == ["git", "push"] for call in process.calls)


def test_the_commit_runs_with_hooks_disabled_and_reports_the_message_it_made(repo):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                                          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "prepare-commit-msg"
    hook.write_text("#!/bin/sh\necho 'Co-Authored-By: Claude <noreply@anthropic.com>' >> \"$1\"\n")
    hook.chmod(0o755)
    (repo / "new.txt").write_text("x\n")
    git.stage_all_and_tree_hash(str(repo))

    sha = git.commit(str(repo), "fix: a change\n\nBody.\n")

    parent, message = git.commit_info(str(repo), sha)
    assert "Co-Authored-By" not in message and message.startswith("fix: a change")
    assert parent == sh(repo, "git", "rev-parse", "HEAD~1")


def test_commit_info_reads_parent_and_message(repo):
    git = GitCli(SubprocessRunner(), env={"PATH": os.environ["PATH"]})
    head = sh(repo, "git", "rev-parse", "HEAD")

    parent, message = git.commit_info(str(repo), head)

    assert parent == "" and message.strip() == "one"
