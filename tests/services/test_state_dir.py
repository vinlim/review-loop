import os
import stat

from review_loop.services.state_dir import ensure_private_dir, readable_by_others


def test_a_new_state_directory_is_created_private_to_the_account(tmp_path):
    path = ensure_private_dir(tmp_path / "home" / ".review-loop")

    assert path.is_dir() and stat.S_IMODE(path.stat().st_mode) == 0o700
    assert not readable_by_others(path)


def test_an_existing_directory_keeps_its_mode_and_is_reported_when_other_accounts_can_read_it(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    os.chmod(path, 0o755)

    ensure_private_dir(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o755 and readable_by_others(path)
