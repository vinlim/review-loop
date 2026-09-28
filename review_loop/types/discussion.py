from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Review:
    id: int
    author: str
    body: str
    state: str
    commit_id: str
    submitted_at: str


@dataclass(frozen=True)
class InlineComment:
    id: int
    author: str
    body: str
    created_at: str
    path: str
    line: int | None
    original_line: int | None
    side: str
    in_reply_to_id: int | None
    review_id: int | None
    commit_id: str
    diff_hunk: str


@dataclass(frozen=True)
class IssueComment:
    id: int
    author: str
    body: str
    created_at: str


@dataclass(frozen=True)
class ReviewThread:
    id: str
    is_resolved: bool
    is_outdated: bool
    path: str
    line: int | None
    comment_ids: list[int]


@dataclass(frozen=True)
class Discussion:
    reviews: list[Review] = field(default_factory=list)
    inline: list[InlineComment] = field(default_factory=list)
    issue_comments: list[IssueComment] = field(default_factory=list)
    threads: list[ReviewThread] = field(default_factory=list)
