"""Runs argument arrays without a shell; a timeout ends the whole process group, not just the parent."""

from __future__ import annotations

import os
import signal
import subprocess
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
        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _terminate_group(process)
            stdout, stderr = _drain(process)
            timed_out = True
        except KeyboardInterrupt:
            _terminate_group(process)  # the agent lives in its own session, so the terminal's interrupt never reached it
            _drain(process)
            raise
        if stdout_path:
            stdout = open(stdout_path).read()
        code = process.returncode if process.returncode is not None else -1
        return CompletedRun(list(argv), code, stdout or "", stderr or "", timed_out=timed_out)


def _terminate_group(process: subprocess.Popen, grace_seconds: float = 2.0) -> None:
    """TERM the whole group, wait briefly, then KILL the whole group: a grandchild that ignores TERM must not survive the leader."""
    _signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.05)
    _signal_group(process, signal.SIGKILL)


def _signal_group(process: subprocess.Popen, signal_number: int) -> None:
    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        pass


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
