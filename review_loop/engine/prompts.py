from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"{{\s*([a-z_]+)\s*}}")


def fill_template(template: str, values: dict[str, str], partial: bool = False) -> str:
    """A placeholder with no value is an error, or stays in place when partial, so a shared block can be filled
    before the phase fills its own."""
    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            if partial:
                return match.group(0)
            raise KeyError(f"prompt placeholder {{{{{name}}}}} has no value")
        return str(values[name])

    return _PLACEHOLDER.sub(substitute, template)
