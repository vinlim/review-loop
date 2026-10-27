"""Paths on the host that ran the loop never reach the PR: the state directory and the home directory name the machine
and the person it belongs to, and a reader of the PR cannot open them."""

from __future__ import annotations

import re
from pathlib import Path

STATE_SHOWN = "<review-loop>"
HOME_SHOWN = "~"


def host_paths(state_dir: str | Path, home: str | Path) -> list[tuple[str, str]]:
    """What outbound text shows in place of the state directory and the home directory."""
    return [(str(state_dir), STATE_SHOWN), (str(home), HOME_SHOWN)]


def redact_paths(text: str, paths: list[tuple[str, str]]) -> str:
    """Each path is rewritten to what it is shown as, longest first so the state directory wins over the home directory
    it sits in. A match ends at a path boundary: `/Users/vin` leaves `/Users/vinlim` and `/Users/vin.bak` alone, and a
    full stop that ends a sentence ends the match."""
    for path, shown in sorted(paths, key=lambda pair: -len(pair[0])):
        path = path.rstrip("/")
        if path:
            text = re.sub(re.escape(path) + r"(?![\w-])(?!\.[\w-])", lambda _: shown, text)
    return text


def shown_command(argv: list[str], checkout: str | Path = "", home: str | Path = "") -> str:
    """A check command as a report shows it: an argument under the checkout is shown relative to it, one under the
    home directory as `~/...`, so a configured interpreter path names neither the machine nor the person."""
    roots = [(str(checkout).rstrip("/"), ""), (str(home).rstrip("/"), f"{HOME_SHOWN}/")]
    shown = []
    for argument in argv:
        for root, prefix in roots:
            if root and argument.startswith(root + "/"):
                argument = prefix + argument[len(root) + 1:]
                break
        shown.append(argument)
    return " ".join(shown)
