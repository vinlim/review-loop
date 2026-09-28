from pathlib import Path

from review_loop.cli.main import main
from review_loop.repositories import runs as runs_repo
from review_loop.services.locks import RunLock
from tests.cli.test_main import URL, container


def test_start_refuses_while_another_coordinator_holds_the_pr_lock(settings, capsys):
    box = container(settings)
    lock = RunLock(settings.state_dir, "webapp", 1004)
    lock.acquire()
    try:
        assert main(["start", URL, "--no-run"], container=box) == 3
    finally:
        lock.release()

    assert runs_repo.list_runs(box.conn) == [] and "another coordinator" in capsys.readouterr().err


def test_restore_works_before_any_configuration_exists(tmp_path, monkeypatch, capsys):
    from review_loop.services.backup import backup_state

    source = tmp_path / "source"
    source.mkdir()
    (source / "config.toml").write_text('state_dir = "x"\n')
    (source / "state.db").write_bytes(b"db")
    archive = backup_state(source, tmp_path / "backups")
    monkeypatch.setenv("REVIEW_LOOP_HOME", str(tmp_path / "fresh-home"))

    assert main(["restore", str(archive), "--to", str(tmp_path / "fresh-home")]) == 0

    assert (tmp_path / "fresh-home" / "config.toml").exists()
