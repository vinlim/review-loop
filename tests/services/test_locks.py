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
