from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PullRef:
    owner: str
    repo: str
    number: int

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"


@dataclass(frozen=True)
class PullRequest:
    ref: PullRef
    title: str
    body: str
    author: str
    state: str
    draft: bool
    merged: bool
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    head_repo: str = ""
