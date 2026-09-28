import os
import time

from review_loop.adapters.process import SubprocessRunner

ENV = {"PATH": os.environ["PATH"]}


def test_run_captures_exit_code_stdout_and_stderr(tmp_path):
    completed = SubprocessRunner().run(["sh", "-c", "echo out; echo err >&2; exit 3"], cwd=str(tmp_path), env=ENV, timeout_seconds=10)

    assert (completed.exit_code, completed.stdout.strip(), completed.stderr.strip(), completed.timed_out) == (3, "out", "err", False)


def test_stdin_is_delivered_to_the_process(tmp_path):
    completed = SubprocessRunner().run(["sh", "-c", "cat"], cwd=str(tmp_path), env=ENV, timeout_seconds=10, stdin="hello\n")

    assert completed.stdout == "hello\n"


def test_a_missing_executable_reports_exit_127_and_names_it(tmp_path):
    completed = SubprocessRunner().run(["definitely-not-installed-xyz", "--version"], cwd=str(tmp_path), env=ENV, timeout_seconds=10)

    assert completed.exit_code == 127 and "definitely-not-installed-xyz" in completed.stderr


def test_a_timeout_kills_the_whole_process_group_and_reports_timed_out(tmp_path):
    pidfile = tmp_path / "child.pid"
    started = time.monotonic()

    completed = SubprocessRunner().run(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], cwd=str(tmp_path), env=ENV, timeout_seconds=1)

    assert completed.timed_out and time.monotonic() - started < 10
    child = int(pidfile.read_text().strip())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the grandchild sleep survived the timeout")


def test_stdout_can_stream_to_a_file_while_the_process_runs(tmp_path):
    import threading

    target = tmp_path / "events.jsonl"
    seen_early = []

    def peek():
        time.sleep(0.4)
        seen_early.append(target.read_text() if target.exists() else "")

    watcher = threading.Thread(target=peek)
    watcher.start()
    completed = SubprocessRunner().run(["sh", "-c", "echo first; sleep 1; echo second"], cwd=str(tmp_path), env=ENV, timeout_seconds=10,
                                       stdout_path=str(target))
    watcher.join()

    assert seen_early == ["first\n"], "the first line must reach the file before the process ends"
    assert target.read_text() == "first\nsecond\n" and completed.stdout == "first\nsecond\n"
