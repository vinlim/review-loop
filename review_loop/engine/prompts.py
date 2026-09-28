from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"{{\s*([a-z_]+)\s*}}")


def fill_template(template: str, values: dict[str, str]) -> str:
    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(f"prompt placeholder {{{{{name}}}}} has no value")
        return str(values[name])

    return _PLACEHOLDER.sub(substitute, template)
