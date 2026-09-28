"""Opt-in checks against the real CLIs and network: `python -m pytest -q -m live`."""

import json
import os
from pathlib import Path

import pytest

from review_loop.adapters.agents.claude import ClaudeAdapter
from review_loop.adapters.github_gh import GhGitHub
from review_loop.adapters.process import SubprocessRunner
from review_loop.cli.doctor import run_doctor
from review_loop.config.settings import load_settings
from review_loop.types.agents import PhaseRequest
from review_loop.types.pull_request import PullRef

pytestmark = pytest.mark.live
HOME = Path(os.environ.get("REVIEW_LOOP_HOME", str(Path.home() / ".review-loop")))
# A merged PR with inline review threads, as owner/repo#number.
LIVE_PR = os.environ.get("REVIEW_LOOP_LIVE_PR", "")
SCHEMAS = Path(__file__).resolve().parents[2] / "review_loop" / "schemas"


def test_doctor_passes_on_this_machine():
    settings = load_settings(HOME / "config.toml")
    checks = run_doctor(SubprocessRunner(), settings, SCHEMAS)

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


def test_claude_runs_no_settings_hook_at_startup_from_the_checkout_or_the_user(tmp_path):
    # SessionStart hooks run before auth, so a scratch CLAUDE_CONFIG_DIR can stand in for user settings with no login.
    checkout, config_dir, ran = tmp_path / "checkout", tmp_path / "claude-config", tmp_path / "ran"
    (checkout / ".claude").mkdir(parents=True)
    config_dir.mkdir()
    ran.mkdir()
    script = checkout / ".claude" / "from-user-hook.sh"
    script.write_text(f"#!/bin/sh\ntouch {ran}/user-hook\n")
    script.chmod(0o755)
    (checkout / ".claude" / "settings.json").write_text(json.dumps(_session_start(f"touch {ran}/project-hook")))
    (config_dir / "settings.json").write_text(json.dumps(_session_start('"$CLAUDE_PROJECT_DIR"/.claude/from-user-hook.sh')))
    request = PhaseRequest(phase="assess", prompt="Reply OK.", schema_path=str(SCHEMAS / "assessment.json"), cwd=str(checkout),
                           env={"HOME": str(Path.home()), "PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": str(config_dir)},
                           timeout_seconds=120, model="haiku", effort="low", output_dir=str(tmp_path / "out"))
    argv = ClaudeAdapter.argv(request)
    every_source = argv.copy()
    every_source[argv.index("--setting-sources") + 1] = "user,project,local"

    _run(every_source, request)
    assert sorted(path.name for path in ran.iterdir()) == ["project-hook", "user-hook"]
    for marker in ran.iterdir():
        marker.unlink()
    _run(argv, request)
    assert list(ran.iterdir()) == []


def _session_start(command: str) -> dict:
    return {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]}}


def _run(argv: list[str], request: PhaseRequest) -> None:
    SubprocessRunner().run(argv, cwd=request.cwd, env=request.env, timeout_seconds=request.timeout_seconds, stdin=request.prompt)
