"""A macOS notification through osascript; falls back to stderr when osascript is unavailable."""

from __future__ import annotations

import os
import sys

from review_loop.types.protocols import ProcessRunner


class MacNotifier:
    def __init__(self, process: ProcessRunner):
        self.process = process

    def notify(self, title: str, message: str) -> None:
        script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
        completed = self.process.run(["osascript", "-e", script], cwd=os.path.expanduser("~"), env=dict(os.environ), timeout_seconds=15)
        if completed.exit_code != 0:
            print(f"[{title}] {message}", file=sys.stderr)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
