import os
import time

from review_loop.adapters.process import SubprocessRunner

ENV = {"PATH": os.environ["PATH"]}


def test_a_grandchild_that_ignores_sigterm_and_holds_the_pipes_cannot_hang_the_timeout(tmp_path):
    started = time.monotonic()

    completed = SubprocessRunner().run(["sh", "-c", "(trap '' TERM; sleep 30) & exit 0"], cwd=str(tmp_path), env=ENV, timeout_seconds=1)

    assert completed.timed_out and time.monotonic() - started < 12
