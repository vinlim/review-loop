"""Opt-in checks against the real CLIs and network: `python -m pytest -q -m live`."""

import os
from pathlib import Path

import pytest

from review_loop.adapters.github_gh import GhGitHub
from review_loop.adapters.process import SubprocessRunner
from review_loop.cli.doctor import run_doctor
from review_loop.config.settings import load_settings
from review_loop.types.pull_request import PullRef

pytestmark = pytest.mark.live
HOME = Path(os.environ.get("REVIEW_LOOP_HOME", str(Path.home() / ".review-loop")))
# A merged PR with inline review threads, as owner/repo#number.
LIVE_PR = os.environ.get("REVIEW_LOOP_LIVE_PR", "")


def test_doctor_passes_on_this_machine():
    settings = load_settings(HOME / "config.toml")
    checks = run_doctor(SubprocessRunner(), settings, Path(__file__).resolve().parents[2] / "review_loop" / "schemas")

    failed = [f"{c.name}: {c.detail}" for c in checks if not c.ok]
    assert failed == []


@pytest.mark.skipif(not LIVE_PR, reason="set REVIEW_LOOP_LIVE_PR=owner/repo#number")
def test_the_gh_gateway_reads_a_merged_pull_request_and_its_threads():
    owner_repo, number = LIVE_PR.split("#")
    ref = PullRef(*owner_repo.split("/"), int(number))
    gateway = GhGitHub(SubprocessRunner(), cwd=str(Path.home()), env=dict(os.environ))

    pull = gateway.fetch_pull(ref)
    discussion = gateway.fetch_discussion(ref)

    assert pull.merged
    assert discussion.reviews and discussion.inline
    assert discussion.threads and all(thread.id.startswith("PRRT_") for thread in discussion.threads)
