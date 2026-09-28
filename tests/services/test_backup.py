from review_loop.services.backup import backup_state, restore_state


def test_backup_archives_the_state_directory_and_restore_reproduces_it_elsewhere(tmp_path):
    state = tmp_path / "state"
    (state / "runs" / "r1").mkdir(parents=True)
    (state / "state.db").write_bytes(b"sqlite")
    (state / "config.toml").write_text('state_dir = "x"\n')
    (state / "runs" / "r1" / "report.md").write_text("done")
    (state / "worktrees" / "webapp-1").mkdir(parents=True)
    (state / "worktrees" / "webapp-1" / "big.bin").write_bytes(b"0" * 10)

    archive = backup_state(state, tmp_path / "backups")
    restored = restore_state(archive, tmp_path / "elsewhere")

    assert archive.exists() and archive.suffix == ".gz"
    assert (restored / "state.db").read_bytes() == b"sqlite"
    assert (restored / "runs" / "r1" / "report.md").read_text() == "done"
    assert (restored / "config.toml").exists()
    assert not (restored / "worktrees").exists(), "worktrees are disposable and stay out of the archive"


def test_restore_refuses_to_overwrite_an_existing_state_directory(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "state.db").write_bytes(b"x")
    archive = backup_state(state, tmp_path / "backups")
    target = tmp_path / "target"
    target.mkdir()
    (target / "state.db").write_bytes(b"keep")

    try:
        restore_state(archive, target)
    except FileExistsError as error:
        assert "target" in str(error)
    else:
        raise AssertionError("expected a refusal")
    assert (target / "state.db").read_bytes() == b"keep"


def test_backups_and_a_restored_state_directory_are_private_to_the_account(tmp_path):
    import stat

    state = tmp_path / "state"
    state.mkdir()
    (state / "config.toml").write_text('state_dir = "x"\n')

    archive = backup_state(state, tmp_path / "backups")
    restored = restore_state(archive, tmp_path / "elsewhere")

    assert stat.S_IMODE((tmp_path / "backups").stat().st_mode) == 0o700
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert stat.S_IMODE(restored.stat().st_mode) == 0o700


def test_restore_tightens_an_existing_empty_destination_before_extracting(tmp_path):
    import os
    import stat

    state = tmp_path / "state"
    state.mkdir()
    (state / "config.toml").write_text('state_dir = "x"\n')
    archive = backup_state(state, tmp_path / "backups")
    target = tmp_path / "shared"
    target.mkdir()
    os.chmod(target, 0o755)

    restored = restore_state(archive, target)

    assert stat.S_IMODE(restored.stat().st_mode) == 0o700 and (restored / "config.toml").exists()


def test_restore_refuses_a_destination_whose_mode_cannot_be_tightened_and_extracts_nothing(tmp_path, monkeypatch):
    import pytest

    from review_loop.services import backup as backup_module

    state = tmp_path / "state"
    state.mkdir()
    (state / "config.toml").write_text('state_dir = "x"\n')
    archive = backup_state(state, tmp_path / "backups")
    target = tmp_path / "volume"
    target.mkdir()
    monkeypatch.setattr(backup_module, "readable_by_others", lambda path: True)

    with pytest.raises(PermissionError, match="cannot be tightened"):
        restore_state(archive, target)

    assert not any(target.iterdir())
