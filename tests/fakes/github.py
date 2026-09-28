from review_loop.types.discussion import Discussion, InlineComment, IssueComment, Review, ReviewThread
from review_loop.types.pull_request import PullRef, PullRequest


class FakeGitHub:
    """An in-memory GitHub: pull requests keyed by number, posts keyed by id; every write is recorded."""

    def __init__(self):
        self.pulls: dict[int, PullRequest] = {}
        self.comments: dict[int, dict] = {}
        self.writes: list[tuple] = []
        self.on_call = None
        self.discussions: dict[int, Discussion] = {}
        self.resolved: set[int] = set()
        self._next_id = 9000

    def add_pull(self, number: int, *, title="A change", body="", author="vinlim", state="open", draft=True,
                 merged=False, base_ref="main", base_sha="b" * 40, head_ref="claude/change", head_sha="h" * 40,
                 owner="acme", repo="webapp", head_repo="") -> PullRequest:
        pull = PullRequest(PullRef(owner, repo, number), title, body, author, state, draft, merged,
                           base_ref, base_sha, head_ref, head_sha, head_repo=head_repo or f"{owner}/{repo}")
        self.pulls[number] = pull
        return pull

    def add_comment(self, number: int, body: str, **fields) -> int:
        self._next_id += 1
        self.comments[self._next_id] = {"id": self._next_id, "pr": number, "body": body, **fields}
        return self._next_id

    def fetch_pull(self, ref: PullRef) -> PullRequest:
        return self.pulls[ref.number]

    def move_head(self, number: int, sha: str) -> None:
        """What GitHub does when the branch is pushed: the PR head follows."""
        import dataclasses

        self.pulls[number] = dataclasses.replace(self.pulls[number], head_sha=sha)

    def fetch_discussion(self, ref: PullRef) -> Discussion:
        if ref.number in self.discussions:
            return self.discussions[ref.number]
        reviews, inline, issues, threads = [], [], [], []
        for comment in self.comments.values():
            if comment["pr"] != ref.number:
                continue
            kind = comment.get("kind", "issue")
            if kind == "review":
                reviews.append(Review(comment["id"], "vinlim", comment["body"], "COMMENTED", comment.get("commit", ""), comment.get("at", "2026-09-27T10:00:00Z")))
            elif kind == "inline":
                inline.append(InlineComment(comment["id"], "vinlim", comment["body"], comment.get("at", "2026-09-27T10:00:00Z"), comment["path"],
                                            comment["line"], comment["line"], "RIGHT", comment.get("reply_to"), comment.get("review_id"), "", ""))
                if comment.get("reply_to") is None:
                    replies = [c["id"] for c in self.comments.values() if c.get("reply_to") == comment["id"]]
                    threads.append(ReviewThread(f"PRRT_{comment['id']}", comment["id"] in self.resolved, False, comment["path"], comment["line"],
                                                [comment["id"], *replies]))
            else:
                issues.append(IssueComment(comment["id"], "vinlim", comment["body"], comment.get("at", "2026-09-27T10:00:00Z")))
        return Discussion(reviews=reviews, inline=inline, issue_comments=issues, threads=threads)

    def post_comment(self, ref: PullRef, body: str) -> dict:
        if self.on_call:
            self.on_call("post_comment")
        comment_id = self.add_comment(ref.number, body)
        self.writes.append(("post_comment", ref.number, body))
        return {"id": comment_id}

    def post_review(self, ref: PullRef, body: str, commit_sha: str, comments: list[dict]) -> dict:
        if self.on_call:
            self.on_call("post_review")
        review_id = self.add_comment(ref.number, body, kind="review", commit=commit_sha)
        comment_ids = [self.add_comment(ref.number, comment["body"], kind="inline", path=comment["path"], line=comment["line"], review_id=review_id)
                       for comment in comments]
        self.writes.append(("post_review", ref.number, body, commit_sha, [c["path"] for c in comments]))
        return {"id": review_id, "comment_ids": comment_ids}

    def reply_to_comment(self, ref: PullRef, comment_id: int, body: str) -> dict:
        if self.on_call:
            self.on_call("reply")
        parent = self.comments[comment_id]
        reply_id = self.add_comment(ref.number, body, kind="inline", path=parent["path"], line=parent["line"], reply_to=comment_id)
        self.writes.append(("reply", comment_id, body))
        return {"id": reply_id}

    def edit_comment(self, ref: PullRef, comment_id: int, body: str) -> dict:
        self.comments[comment_id] = {"id": comment_id, "pr": ref.number, "body": body}
        self.writes.append(("edit_comment", comment_id, body))
        return {"id": comment_id}

    def resolve_thread(self, thread_id: str) -> dict:
        self.writes.append(("resolve_thread", thread_id))
        self.resolved.add(int(thread_id.removeprefix("PRRT_")))
        return {"resolved": True}

    def find_post_by_marker(self, ref: PullRef, marker: str) -> dict | None:
        for comment in self.comments.values():
            if comment["pr"] == ref.number and marker in comment["body"]:
                return {"id": comment["id"], "kind": "comment"}
        return None
