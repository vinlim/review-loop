"""The patch kept for a discarded fix restores it exactly: trailing blank context, CRLF lines and binary files included."""

import os
import subprocess

from review_loop.adapters.git_cli import GitCli
from review_loop.adapters.process import SubprocessRunner


def test_the_written_patch_applied_to_the_reset_worktree_reproduces_the_discarded_tree(tmp_path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1"}  # no user diff settings either

    def sh(*argv):
        return subprocess.run(argv, cwd=repo, env=env, check=True, capture_output=True).stdout.decode().strip()

    repo = tmp_path / "repo"
    repo.mkdir()
    sh("git", "init", "-q", "-b", "main")
    sh("git", "config", "user.email", "t@example.com")
    sh("git", "config", "user.name", "t")
    (repo / "crlf.txt").write_bytes(b"first\r\nsecond\r\n")
    (repo / "image.bin").write_bytes(bytes(range(256)))
    (repo / "trailing.txt").write_text("one\ntwo\nthree\n\n\n")  # sorts last, so its hunk ends the patch
    sh("git", "add", "-A")
    sh("git", "commit", "-q", "-m", "base")
    head = sh("git", "rev-parse", "HEAD")
    (repo / "crlf.txt").write_bytes(b"first\r\nchanged\r\n")
    (repo / "image.bin").write_bytes(bytes(reversed(range(256))))
    (repo / "new.bin").write_bytes(b"\x00\x01binary\xff")
    (repo / "trailing.txt").write_text("one\nTWO\nthree\n\n\n")  # the hunk ends on blank context lines
    git = GitCli(SubprocessRunner(), env=env)
    tree = git.working_tree_hash(str(repo))
    patch = tmp_path / "discarded-fix-1.patch"

    git.write_patch(str(repo), head, tree, str(patch))
    sh("git", "reset", "-q", "--hard", head)
    sh("git", "clean", "-q", "-fd")
    sh("git", "apply", str(patch))

    assert git.working_tree_hash(str(repo)) == tree
