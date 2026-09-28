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
