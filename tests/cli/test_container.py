import stat

from review_loop.cli import container as container_module
from tests.conftest import MINIMAL_TOML


def test_build_container_creates_a_private_state_directory(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(MINIMAL_TOML.replace("{state_dir}", str(tmp_path / "state")).replace("{local_path}", str(tmp_path)))
    monkeypatch.setattr(container_module, "tool_versions", lambda process, env: {"review-loop": "test"})

    box = container_module.build_container(home)

    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700 and box.versions == {"review-loop": "test"}


def test_claiming_the_claude_login_removes_it_from_the_process_environment(monkeypatch):
    import os

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "author-token")

    assert container_module.claim_claude_oauth_token() == "author-token"

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ and container_module.claim_claude_oauth_token() == ""


def test_build_container_carries_the_claimed_login_and_probes_tools_without_it(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(MINIMAL_TOML.replace("{state_dir}", str(tmp_path / "state")).replace("{local_path}", str(tmp_path)))
    probed = {}
    monkeypatch.setattr(container_module, "tool_versions", lambda process, env: probed.update(env) or {"review-loop": "test"})
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    box = container_module.build_container(home, claude_oauth_token="author-token")

    assert box.claude_oauth_token == "author-token"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in probed and "CLAUDE_CODE_OAUTH_TOKEN" not in box.git.env
