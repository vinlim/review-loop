import os
import signal
import subprocess
import time

import pytest

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


def test_zombies_and_processes_already_exiting_are_not_survivors():
    listing = "100 100 S\n101 100 RE\n102 100 Z\n103 100 Ss+\n104 999 S\n105 100 X\n"

    assert process_module._live_in(listing, 100) == {100, 103}


def test_a_refused_kill_when_ps_cannot_list_the_group_says_so(tmp_path, monkeypatch):
    _kill_refused(monkeypatch)

    def no_ps(*args, **kwargs):
        raise OSError("ps unavailable")
    monkeypatch.setattr(process_module.subprocess, "run", no_ps)

    completed = SubprocessRunner().run(["sh", "-c", "sleep 30"], cwd=str(tmp_path), env=ENV, timeout_seconds=1)

    assert completed.timed_out and "ps could not list" in completed.cleanup_warning
    assert completed.cleanup_warning in completed.stderr


def test_the_leader_is_not_reaped_before_the_kill_so_its_group_id_cannot_be_recycled(tmp_path, monkeypatch):
    seen = []

    def killpg(pgid, signal_number):
        if signal_number == signal.SIGKILL:
            seen.append(subprocess.run(["ps", "-o", "stat=", "-p", str(pgid)], capture_output=True, text=True).stdout.strip())
            raise PermissionError(1, "Operation not permitted")
        REAL_KILLPG(pgid, signal_number)
    monkeypatch.setattr(process_module.os, "killpg", killpg)

    SubprocessRunner().run(["sh", "-c", "sleep 30"], cwd=str(tmp_path), env=ENV, timeout_seconds=1)

    assert seen and seen[0].startswith("Z"), "the exited leader must still hold its pid when the KILL goes out"


def test_an_interrupt_reports_a_member_that_refused_kill(tmp_path, monkeypatch, capsys):
    _kill_refused(monkeypatch)
    monkeypatch.setattr(process_module, "_live_members", lambda pgid: {4242})
    real_communicate = subprocess.Popen.communicate
    calls = []

    def communicate(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real_communicate(self, *args, **kwargs)
    monkeypatch.setattr(subprocess.Popen, "communicate", communicate)

    with pytest.raises(KeyboardInterrupt):
        SubprocessRunner().run(["sh", "-c", "sleep 30"], cwd=str(tmp_path), env=ENV, timeout_seconds=30)

    assert "still running: 4242" in capsys.readouterr().err
