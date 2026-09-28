from __future__ import annotations

import re

from review_loop.types.pull_request import PullRef

_PULL_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")


class PullUrlError(ValueError):
    pass


def parse_pull_url(url: str) -> PullRef:
    match = _PULL_URL.match(url.strip())
    if not match:
        raise PullUrlError(f"not a pull request URL: {url!r}; expected https://github.com/<owner>/<repo>/pull/<number>")
    owner, repo, number = match.groups()
    return PullRef(owner=owner, repo=repo, number=int(number))
