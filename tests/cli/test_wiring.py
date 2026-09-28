from pathlib import Path

from review_loop.adapters.agents.claude import ClaudeAdapter
from review_loop.adapters.agents.codex import CodexAdapter
from review_loop.cli.wiring import build_deps
from tests.cli.test_main import container


def test_build_deps_wires_the_real_agent_adapters_and_the_state_paths(settings, tmp_path):
    box = container(settings)

    deps = build_deps(box, inspect_only=True, platform="darwin")

    assert isinstance(deps.reviewer, CodexAdapter) and isinstance(deps.author, ClaudeAdapter)
    assert deps.runs_dir == settings.state_dir / "runs" and deps.inspect_only
    assert deps.prompts_dir.joinpath("review-initial.md").exists() and deps.schemas_dir.joinpath("review.json").exists() and deps.prompts_dir.parent.name == "review_loop"
    assert type(deps.notifier).__name__ == "MacNotifier"
    assert "PATH" in deps.base_env


def test_build_deps_picks_the_stdout_notifier_off_macos(settings):
    deps = build_deps(container(settings), inspect_only=False, platform="linux")

    assert type(deps.notifier).__name__ == "StdoutNotifier"


def test_the_author_session_finder_scans_the_claude_projects_dir(settings, tmp_path, monkeypatch):
    projects = tmp_path / "projects" / "-Users-x-webapp"
    projects.mkdir(parents=True)
    (projects / "abc.jsonl").write_text('{"type":"user","sessionId":"abc","gitBranch":"claude/change","cwd":"/x"}\n')
    deps = build_deps(container(settings), inspect_only=False, platform="linux", claude_projects_dir=tmp_path / "projects")

    assert deps.find_author_session("claude/change") == "abc"
    assert deps.find_author_session("other") == ""


def test_build_deps_hands_the_claimed_login_to_the_claude_adapter(settings):
    box = container(settings)
    box.claude_oauth_token = "author-token"

    deps = build_deps(box, inspect_only=False, platform="linux")

    assert deps.author.oauth_token == "author-token"
