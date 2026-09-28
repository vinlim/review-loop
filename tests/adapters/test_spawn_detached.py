"""The detached coordinator lives in its own session with its output in a log, so the shell that started it can go away."""

import os
import sys

from review_loop.adapters.process import spawn_detached


def test_a_detached_process_gets_its_own_session_and_writes_to_the_log(tmp_path):
    log_path = tmp_path / "coordinator.log"
    log_path.write_text("earlier line\n")

    pid = spawn_detached([sys.executable, "-c", "import os; print('session', os.getsid(0)); print('to stderr', file=__import__('sys').stderr)"],
                         cwd=str(tmp_path), env={"PATH": os.environ.get("PATH", "")}, log_path=log_path)
    os.waitpid(pid, 0)

    lines = log_path.read_text().splitlines()
    assert lines[0] == "earlier line" and "to stderr" in lines
    child_session = int(next(line for line in lines if line.startswith("session ")).split()[1])
    assert child_session == pid and child_session != os.getsid(0)
