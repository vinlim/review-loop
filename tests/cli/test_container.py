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
