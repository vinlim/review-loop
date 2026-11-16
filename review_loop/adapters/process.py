"""Runs argument arrays without a shell; a timeout ends the whole process group, not just the parent."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from review_loop.types.protocols import CompletedRun


class SubprocessRunner:
    def run(self, argv: list[str], cwd: str, env: dict[str, str], timeout_seconds: int, stdin: str = "",
            stdout_path: str | None = None) -> CompletedRun:
        """With stdout_path, stdout goes straight to that file as it is produced, so a long run can be watched."""
        sink = open(stdout_path, "w") if stdout_path else None
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=sink or subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, start_new_session=True)
        except FileNotFoundError:
            return CompletedRun(list(argv), 127, "", f"not found: {argv[0]}")
        finally:
            if sink:
                sink.close()
        timed_out = False
        warning = ""
        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            warning = _survivor_warning(process.pid, _terminate_group(process))
            stdout, stderr = _drain(process)
            timed_out = True
        except KeyboardInterrupt:
            # the agent lives in its own session, so the terminal's interrupt never reached it
            warning = _survivor_warning(process.pid, _terminate_group(process))
            _drain(process)
            if warning:
                print(warning, file=sys.stderr)
            raise
        if stdout_path:
            stdout = open(stdout_path).read()
        code = process.returncode if process.returncode is not None else -1
        stderr = "\n".join(part for part in (stderr or "", warning) if part)
        return CompletedRun(list(argv), code, stdout or "", stderr, timed_out=timed_out, cleanup_warning=warning)


def spawn_detached(argv: list[str], cwd: str, env: dict[str, str], log_path) -> int:
    """Start argv in its own session with its output appended to log_path, so it outlives this process and the
    process group that started it; returns the pid. Nothing is inherited but the environment given."""
    with open(log_path, "a") as log:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, close_fds=True)
    return process.pid


def _terminate_group(process: subprocess.Popen, grace_seconds: float = 2.0) -> set[int] | None:
    """TERM the whole group, wait briefly, then KILL the whole group: a grandchild that ignores TERM must not survive the leader.
    The leader stays unreaped until after the KILL, so its pid, which is the group id, cannot be recycled in between.
    Returns the live members the KILL could not reach, or None when ps cannot say."""
    _signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and not _leader_exited(process):
        time.sleep(0.05)
    return _signal_group(process, signal.SIGKILL)


def _leader_exited(process: subprocess.Popen) -> bool:
    """Whether the leader has exited, without reaping it."""
    if process.returncode is not None:
        return True
    try:
        return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True


def _signal_group(process: subprocess.Popen, signal_number: int) -> set[int] | None:
    """Returns the live members the signal could not reach: empty when it landed or the group is gone, None when ps cannot say."""
    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        pass
    except PermissionError:
        # EPERM means no member took the signal. On macOS that includes a group left with only zombies, which is how a
        # group looks right after TERM worked; only a live member we may not signal is a real survivor.
        return _live_members(process.pid)
    return set()


def _live_members(pgid: int) -> set[int] | None:
    try:
        listing = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _live_in(listing.stdout, pgid) if listing.returncode == 0 else None


def _live_in(listing: str, pgid: int) -> set[int]:
    """Group members in `ps -o pid=,pgid=,stat=` output, leaving out zombies (Z, or X on Linux) and processes macOS flags E for exiting."""
    live = set()
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == str(pgid) and fields[2][0] not in "ZX" and "E" not in fields[2]:
            live.add(int(fields[0]))
    return live


def _survivor_warning(pgid: int, survivors: set[int] | None) -> str:
    if survivors is None:
        return f"process group {pgid} refused KILL and ps could not list what is left of it"
    if survivors:
        return f"process group {pgid} refused KILL; still running: {', '.join(str(pid) for pid in sorted(survivors))}"
    return ""


def _drain(process: subprocess.Popen, seconds: float = 5.0) -> tuple[str, str]:
    """A survivor holding the pipes cannot block the coordinator: give up on the pipes after a bounded wait."""
    try:
        return process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            pass
        return "", "output abandoned: a child process kept the pipes open after the timeout"
