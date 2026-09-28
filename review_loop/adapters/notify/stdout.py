import sys


class StdoutNotifier:
    def notify(self, title: str, message: str) -> None:
        print(f"[{title}] {message}", file=sys.stderr)
