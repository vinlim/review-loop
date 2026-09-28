"""Who said what on a pull request: markers first, the manual era's shapes second, humans otherwise."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum

from review_loop.types.discussion import Discussion, InlineComment, IssueComment, Review, ReviewThread


class Role(StrEnum):
    REVIEWER = "reviewer"
    AUTHOR = "author"
    HUMAN = "human"


@dataclass(frozen=True)
class Marker:
    role: Role
    run_id: str
    pass_no: int
    kind: str
    finding_id: str = ""


_MARKER = re.compile(r"<!-- review-loop role=(\w+) run=(\S+) pass=(\d+) kind=(\S+?)(?: finding=(\S+))? -->")

_REVIEWER_SHAPES = (
    re.compile(r"\AEXECUTIVE SUMMARY\b"),
    re.compile(r"\A\[(BLOCKER|ISSUE|CHORE|QUESTION)\]"),
    re.compile(r"\AFile: .+\nSeverity: \[(BLOCKER|ISSUE|CHORE|QUESTION)\]"),
    re.compile(r"\A(Follow-up review|Verified at|Still open|Not fixed|Re-checked)\b"),
)
_AUTHOR_SHAPES = (
    re.compile(r"\A## Review response\b"),
    re.compile(r"\A(Accepted|Finding accepted|Fixed|Rejected|Declined|Answered|Addressed|Resolved|Both fixed|Not changed|Agreed|Taken|Confirmed|Correct|Not taking)\b"),
)


def make_marker(role: Role, run_id: str, pass_no: int, kind: str, finding_id: str = "") -> str:
    finding = f" finding={finding_id}" if finding_id else ""
    return f"<!-- review-loop role={role.value} run={run_id} pass={pass_no} kind={kind}{finding} -->"


def parse_marker(body: str) -> Marker | None:
    match = _MARKER.search(body)
    if not match:
        return None
    role, run_id, pass_no, kind, finding_id = match.groups()
    if role not in Role.__members__.values():
        return None
    return Marker(Role(role), run_id, int(pass_no), kind, finding_id or "")


def classify_role(body: str, author: str, pr_author: str, *, root_marked: bool = False,
                  previous_role: Role | None = None, trusted_logins: set[str] | None = None) -> Role:
    """A marker decides, when a trusted login posted it. Without one: a human, unless the post is a
    manual-era post by the PR author whose shape gives it away. In a thread the tool started, every
    unmarked reply is a human."""
    marker = parse_marker(body)
    trusted = trusted_logins if trusted_logins is not None else {pr_author}
    if marker and author in trusted:
        return marker.role
    if marker:
        return Role.HUMAN
    if author != pr_author or root_marked:
        return Role.HUMAN
    text = body.lstrip()
    if any(shape.search(text) for shape in _REVIEWER_SHAPES):
        return Role.REVIEWER
    if any(shape.search(text) for shape in _AUTHOR_SHAPES):
        return Role.AUTHOR
    if previous_role in (Role.REVIEWER, Role.AUTHOR):
        return Role.AUTHOR if previous_role == Role.REVIEWER else Role.REVIEWER
    return Role.HUMAN


def render_discussion(discussion: Discussion, pr_author: str, trusted_logins: set[str] | None = None) -> str:
    entries = []
    for review in discussion.reviews:
        if not review.body.strip():
            continue
        role = classify_role(review.body, review.author, pr_author, trusted_logins=trusted_logins)
        entries.append((review.submitted_at, 0, f"### [{role}] review #{review.id} ({review.state}, {review.commit_id[:9]}, {review.submitted_at})\n\n{review.body.strip()}\n"))
    threads_by_root = {thread.comment_ids[0]: thread for thread in discussion.threads if thread.comment_ids}
    for root, replies in _threads(discussion.inline):
        entries.append((root.created_at, 1, _render_thread(root, replies, threads_by_root.get(root.id), pr_author, trusted_logins)))
    for comment in discussion.issue_comments:
        role = classify_role(comment.body, comment.author, pr_author, trusted_logins=trusted_logins)
        entries.append((comment.created_at, 2, f"### [{role}] comment #{comment.id} ({comment.created_at})\n\n{comment.body.strip()}\n"))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    return "\n".join(text for _, _, text in entries)


def digest(discussion: Discussion) -> str:
    """Order-independent fingerprint of everything that can change a decision."""
    material = {
        "reviews": sorted((r.id, r.state, _hash(r.body)) for r in discussion.reviews),
        "inline": sorted((c.id, c.in_reply_to_id or 0, _hash(c.body)) for c in discussion.inline),
        "issue": sorted((c.id, _hash(c.body)) for c in discussion.issue_comments),
        "threads": sorted((t.id, t.is_resolved, t.is_outdated) for t in discussion.threads),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _threads(comments: list[InlineComment]) -> list[tuple[InlineComment, list[InlineComment]]]:
    roots = sorted((c for c in comments if c.in_reply_to_id is None), key=lambda c: (c.created_at, c.id))
    replies = {}
    for comment in comments:
        if comment.in_reply_to_id is not None:
            replies.setdefault(comment.in_reply_to_id, []).append(comment)
    return [(root, sorted(replies.get(root.id, []), key=lambda c: (c.created_at, c.id))) for root in roots]


def _render_thread(root: InlineComment, replies: list[InlineComment], thread: ReviewThread | None, pr_author: str,
                   trusted_logins: set[str] | None = None) -> str:
    root_role = classify_role(root.body, root.author, pr_author, trusted_logins=trusted_logins)
    root_marked = parse_marker(root.body) is not None
    location = f"{root.path}:{root.line if root.line is not None else root.original_line}"
    state = ", resolved" if thread and thread.is_resolved else ""
    outdated = ", outdated" if thread and thread.is_outdated else ""
    lines = [f"### [{root_role}] inline #{root.id} at {location} ({root.created_at}{state}{outdated})", "", root.body.strip(), ""]
    previous = root_role
    for reply in replies:
        role = classify_role(reply.body, reply.author, pr_author, root_marked=root_marked, previous_role=previous, trusted_logins=trusted_logins)
        lines += [f"#### [{role}] reply #{reply.id} ({reply.created_at})", "", reply.body.strip(), ""]
        previous = role
    return "\n".join(lines)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]
