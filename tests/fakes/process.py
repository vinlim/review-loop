from review_loop.types.protocols import CompletedRun


class FakeProcessRunner:
    """Maps an argv prefix to a scripted result; records every call with its cwd and env."""

    def __init__(self, log: list | None = None):
        self.scripts: list[tuple[tuple[str, ...], CompletedRun | list[CompletedRun]]] = []
        self.calls: list[dict] = []
        self.log = log if log is not None else []
        self.on_run = None

    def script(self, prefix: list[str], stdout: str = "", exit_code: int = 0, stderr: str = "", timed_out: bool = False) -> None:
        self.scripts.append((tuple(prefix), CompletedRun(list(prefix), exit_code, stdout, stderr, timed_out)))

    def script_sequence(self, prefix: list[str], results: list[CompletedRun]) -> None:
        self.scripts.append((tuple(prefix), list(results)))

    def run(self, argv: list[str], cwd: str, env: dict[str, str], timeout_seconds: int, stdin: str = "",
            stdout_path: str | None = None) -> CompletedRun:
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": dict(env), "timeout": timeout_seconds, "stdin": stdin, "stdout_path": stdout_path})
        self.log.append(("process", list(argv)))
        if self.on_run is not None:
            self.on_run(self.calls[-1])
        for prefix, result in self.scripts:
            if tuple(argv[: len(prefix)]) == prefix:
                if isinstance(result, list):
                    completed = result.pop(0) if len(result) > 1 else result[0]
                else:
                    completed = CompletedRun(list(argv), result.exit_code, result.stdout, result.stderr, result.timed_out)
                if stdout_path:
                    open(stdout_path, "w").write(completed.stdout)
                return completed
        raise AssertionError(f"no script for argv {argv!r}")
