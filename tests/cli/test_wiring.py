from pathlib import Path

from review_loop.adapters.agents.agy import AgyAdapter
from review_loop.adapters.agents.claude import ClaudeAdapter
from review_loop.adapters.agents.codex import CodexAdapter
from review_loop.adapters.agents.opencode import OpencodeAdapter
from review_loop.cli.wiring import build_deps
from review_loop.types.agents import AGENT_PROFILES
from tests.cli.test_main import container


def test_build_deps_wires_the_real_agent_adapters_and_the_state_paths(settings, tmp_path):
    box = container(settings)

    deps = build_deps(box, inspect_only=True, platform="darwin")

    assert isinstance(deps.agents["codex"], CodexAdapter) and isinstance(deps.agents["claude"], ClaudeAdapter)
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

    assert deps.find_author_session("claude", "claude/change") == "abc"
    assert deps.find_author_session("claude", "other") == ""
    assert deps.find_author_session("opencode", "claude/change") == ""


def test_every_agent_the_config_accepts_has_an_adapter(settings):
    deps = build_deps(container(settings), inspect_only=False, platform="linux")

    assert set(deps.agents) == set(AGENT_PROFILES)
    assert isinstance(deps.agents["agy"], AgyAdapter) and isinstance(deps.agents["opencode"], OpencodeAdapter)
