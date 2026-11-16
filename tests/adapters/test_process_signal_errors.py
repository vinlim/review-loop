import os
import signal
import subprocess
import time

from review_loop.adapters import process as process_module
from review_loop.adapters.process import SubprocessRunner

ENV = {"PATH": os.environ["PATH"]}
REAL_KILLPG = os.killpg


def _kill_refused(monkeypatch):
    """KILL fails with EPERM, as macOS reports for a group whose remaining members are zombies; TERM goes through."""
    def killpg(pgid, signal_number):
        if signal_number == signal.SIGKILL:
            raise PermissionError(1, "Operation not permitted")
        REAL_KILLPG(pgid, signal_number)
    monkeypatch.setattr(process_module.os, "killpg", killpg)


def test_a_refused_kill_after_term_worked_still_ends_as_a_timed_out_run(tmp_path, monkeypatch):
    _kill_refused(monkeypatch)

    completed = SubprocessRunner().run(["sh", "-c", "sleep 30"], cwd=str(tmp_path), env=ENV, timeout_seconds=1)

    assert completed.timed_out and "still running" not in completed.stderr


def test_a_refused_kill_names_a_member_that_is_still_alive(tmp_path, monkeypatch):
    _kill_refused(monkeypatch)
    pidfile = tmp_path / "survivor.pid"
    script = f"(trap '' TERM; exec sleep 30) >/dev/null 2>&1 </dev/null & echo $! > {pidfile}; wait"
    try:
        completed = SubprocessRunner().run(["sh", "-c", script], cwd=str(tmp_path), env=ENV, timeout_seconds=1)
        survivor = int(pidfile.read_text().strip())

        assert completed.timed_out
        assert f"still running: {survivor}" in completed.stderr
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_zombie_members_do_not_count_as_alive(tmp_path):
    leader = subprocess.Popen(["sh", "-c", "exit 0"], start_new_session=True)
    try:
        time.sleep(0.3)  # exited but not reaped: a zombie that still holds its group

        assert process_module._live_members(leader.pid) == set()
    finally:
        leader.wait()
