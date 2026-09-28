import pytest

from review_loop.services.locks import AlreadyLocked, RunLock


def test_two_coordinators_cannot_both_hold_the_lock_for_one_repository_and_pr(tmp_path):
    first = RunLock(tmp_path, "webapp", 1004)
    second = RunLock(tmp_path, "webapp", 1004)
    first.acquire()

    with pytest.raises(AlreadyLocked):
        second.acquire()

    first.release()
    second.acquire()
    second.release()


def test_a_lock_for_another_pr_is_independent(tmp_path):
    RunLock(tmp_path, "webapp", 1004).acquire()

    other = RunLock(tmp_path, "webapp", 1005)
    other.acquire()
    other.release()


def test_is_held_reports_another_coordinators_lock_without_taking_or_disturbing_it(tmp_path):
    lock = RunLock(tmp_path, "webapp", 1004)
    assert lock.is_held() is False

    lock.acquire()

    assert RunLock(tmp_path, "webapp", 1004).is_held() is True
    with pytest.raises(AlreadyLocked):
        RunLock(tmp_path, "webapp", 1004).acquire()
    lock.release()
    assert lock.is_held() is False


def test_is_held_on_a_pr_that_never_had_a_coordinator_creates_no_lock_file(tmp_path):
    assert RunLock(tmp_path, "webapp", 1005).is_held() is False

    assert not (tmp_path / "locks" / "webapp-1005.lock").exists()
