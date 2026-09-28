"""GitHub through the gh CLI: REST for the three comment surfaces, GraphQL for thread state."""

from __future__ import annotations

import json
import re
from typing import Any

from review_loop.types.discussion import Discussion, InlineComment, IssueComment, Review, ReviewThread
from review_loop.types.protocols import ProcessRunner
from review_loop.types.pull_request import PullRef, PullRequest

_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved isOutdated path line comments(first: 100) { nodes { databaseId } } }
      }
    }
  }
}
"""


class GhGitHub:
    def __init__(self, process: ProcessRunner, cwd: str, env: dict[str, str] | None = None, timeout_seconds: int = 120):
        self.process = process
        self.cwd = cwd
        self.env = env if env is not None else {}
        self.timeout_seconds = timeout_seconds

    def fetch_pull(self, ref: PullRef) -> PullRequest:
        data = self._api(f"repos/{ref.owner}/{ref.repo}/pulls/{ref.number}")
        return PullRequest(
            ref=ref, title=data["title"], body=data.get("body") or "", author=data["user"]["login"],
            state=data["state"], draft=bool(data.get("draft")), merged=bool(data.get("merged")),
            base_ref=data["base"]["ref"], base_sha=data["base"]["sha"],
            head_ref=data["head"]["ref"], head_sha=data["head"]["sha"],
            head_repo=((data["head"].get("repo") or {}).get("full_name") or ""),
        )

    def fetch_discussion(self, ref: PullRef) -> Discussion:
        base = f"repos/{ref.owner}/{ref.repo}"
        reviews = [
            Review(r["id"], r["user"]["login"], r.get("body") or "", r["state"], r.get("commit_id") or "", r.get("submitted_at") or "")
            for r in self._paginated(f"{base}/pulls/{ref.number}/reviews")
        ]
        inline = [
            InlineComment(c["id"], c["user"]["login"], c.get("body") or "", c["created_at"], c["path"], c.get("line"),
                          c.get("original_line"), c.get("side") or "RIGHT", c.get("in_reply_to_id"),
                          c.get("pull_request_review_id"), c.get("commit_id") or "", c.get("diff_hunk") or "")
            for c in self._paginated(f"{base}/pulls/{ref.number}/comments")
        ]
        issue_comments = [
            IssueComment(c["id"], c["user"]["login"], c.get("body") or "", c["created_at"])
            for c in self._paginated(f"{base}/issues/{ref.number}/comments")
        ]
        return Discussion(reviews=reviews, inline=inline, issue_comments=issue_comments, threads=self._threads(ref))

    def post_review(self, ref: PullRef, body: str, commit_sha: str, comments: list[dict]) -> dict:
        payload = {"commit_id": commit_sha, "body": body, "event": "COMMENT",
                   "comments": [{"path": c["path"], "line": int(c["line"]), "side": "RIGHT", "body": c["body"]} for c in comments]}
        review = self._post(f"repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/reviews", payload)
        comment_ids: list = []
        if comments:
            posted = self._paginated(f"repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/reviews/{review['id']}/comments")
            comment_ids = _match_comment_ids(comments, posted)
        return {"id": review["id"], "comment_ids": comment_ids}

    def reply_to_comment(self, ref: PullRef, comment_id: int, body: str) -> dict:
        reply = self._post(f"repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/comments/{comment_id}/replies", {"body": body})
        return {"id": reply["id"]}

    def post_comment(self, ref: PullRef, body: str) -> dict:
        comment = self._post(f"repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments", {"body": body})
        return {"id": comment["id"]}

    def edit_comment(self, ref: PullRef, comment_id: int, body: str) -> dict:
        comment = self._run(["gh", "api", "-X", "PATCH", f"repos/{ref.owner}/{ref.repo}/issues/comments/{comment_id}", "--input", "-"],
                            stdin=json.dumps({"body": body}))
        return {"id": comment["id"]}

    def resolve_thread(self, thread_id: str) -> dict:
        mutation = "mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }"
        data = self._run(["gh", "api", "graphql", "-f", f"query={mutation}", "-f", f"id={thread_id}"])
        return {"resolved": bool(data["data"]["resolveReviewThread"]["thread"]["isResolved"])}

    def find_post_by_marker(self, ref: PullRef, marker: str) -> dict | None:
        discussion = self.fetch_discussion(ref)
        for review in discussion.reviews:
            if marker in review.body:
                return {"id": review.id, "kind": "review"}
        for comment in discussion.inline:
            if marker in comment.body:
                return {"id": comment.id, "kind": "inline"}
        for comment in discussion.issue_comments:
            if marker in comment.body:
                return {"id": comment.id, "kind": "issue_comment"}
        return None

    def _post(self, path: str, payload: dict) -> Any:
        return self._run(["gh", "api", "-X", "POST", path, "--input", "-"], stdin=json.dumps(payload))


        return self._run(["gh", "api", "-X", "POST", path, "--input", "-"], stdin=json.dumps(payload))

    def _threads(self, ref: PullRef) -> list[ReviewThread]:
        threads: list[ReviewThread] = []
        after = ""
        while True:
            argv = ["gh", "api", "graphql", "-f", f"query={_THREADS_QUERY}", "-f", f"owner={ref.owner}",
                    "-f", f"name={ref.repo}", "-F", f"number={ref.number}"]
            if after:
                argv += ["-f", f"after={after}"]
            page = self._run(argv)["data"]["repository"]["pullRequest"]["reviewThreads"]
            for node in page["nodes"]:
                threads.append(ReviewThread(node["id"], bool(node["isResolved"]), bool(node["isOutdated"]),
                                            node.get("path") or "", node.get("line"),
                                            [c["databaseId"] for c in node["comments"]["nodes"]]))
            if not page["pageInfo"]["hasNextPage"]:
                return threads
            after = page["pageInfo"]["endCursor"]

    def _paginated(self, path: str) -> list[dict[str, Any]]:
        pages = self._api(path, "--paginate", "--slurp")
        return [item for page in pages for item in page]

    def _api(self, path: str, *extra: str) -> Any:
        return self._run(["gh", "api", path, *extra])

    def _run(self, argv: list[str], stdin: str = "") -> Any:
        completed = self.process.run(argv, cwd=self.cwd, env=self.env, timeout_seconds=self.timeout_seconds, stdin=stdin)
        if completed.exit_code != 0:
            raise RuntimeError(f"{' '.join(argv[:3])} failed (exit {completed.exit_code}): {completed.stderr.strip()}")
        return json.loads(completed.stdout)


_MARKER = re.compile(r"<!-- review-loop[^>]*-->")


def _match_comment_ids(requested: list[dict], posted: list[dict]) -> list:
    """Map each requested comment to the posted one carrying its marker; position is only the fallback."""
    ids = []
    for index, comment in enumerate(requested):
        marker = _MARKER.search(comment["body"])
        match = next((item for item in posted if marker and marker.group(0) in (item.get("body") or "")), None)
        ids.append(match["id"] if match else (posted[index]["id"] if index < len(posted) else None))
    return ids
