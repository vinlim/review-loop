"""Ending a run: the report on disk, the terminal comment on the PR, and a notification."""

from __future__ import annotations

from review_loop.engine.discussion import Role, make_marker
from review_loop.engine.report import fit_for_comment, render_report
from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import inbox as inbox_repo
from review_loop.repositories import outbox as outbox_repo
from review_loop.repositories import verification as verification_repo
from review_loop.services.phase_support import Deps, inspect_only, now, publisher as make_publisher, ref, remote_head, save
from review_loop.types.run import Outcome, Run, RunState

# GitHub rejects a comment over 65,536 characters; the difference leaves room for the marker. A cut report names
# its full copy relative to the state directory, so the runner's home path never reaches the PR.
COMMENT_LIMIT = 60_000


def _pause_head_changed(deps: Deps, run: Run) -> bool:
    from review_loop.services.phase_support import pause
    from review_loop.types.run import PauseReason

    pause(deps, run, PauseReason.HEAD_CHANGED, resume_state=run.state)
    return True


def complete_run(deps: Deps, run: Run, outcome: Outcome, exhausted: bool) -> Run:
    run.outcome = outcome
    findings = findings_repo.list_findings(deps.conn, run.id)
    events: dict[str, list[dict]] = {}
    for event in findings_repo.list_events(deps.conn, run.id):
        events.setdefault(event["finding_id"], []).append(event)
    inbox_items = [item for item in inbox_repo.list_items(deps.conn, repo=run.repo) if item["source_pr"] == run.pr_number]
    report = render_report(run, findings, events, decisions_repo.list_decisions(deps.conn, run.id),
                           verification_repo.list_results(deps.conn, run.id), inbox_items, exhausted,
                           commits=_loop_commits(deps, run, events), finished_at=now(deps))
    report_path = deps.runs_dir / run.id / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    marker = make_marker(Role.AUTHOR, run.id, run.pass_no, "final")
    publishing = not inspect_only(deps, run) and deps.settings.repositories[run.repo].publication.post_author_responses
    if publishing:
        pull_ref = ref(run)
        pull = deps.github.fetch_pull(pull_ref)
        if pull.state != "open":
            return save(deps, run, RunState.CANCELLED)
        if pull.head_sha != remote_head(run):
            return save(deps, run, RunState.PAUSED) if _pause_head_changed(deps, run) else run
        publisher = make_publisher(deps, run)
        try:
            publisher.reconcile(run.id, pull_ref)
            if not outbox_repo.has_done(deps.conn, run.id, marker):
                shown_path = report_path.relative_to(deps.runs_dir.parent).as_posix()
                publisher.post(run.id, "final", {"outcome": outcome.value}, marker,
                               fit_for_comment(report, COMMENT_LIMIT, shown_path) + "\n" + marker,
                               lambda cleaned: deps.github.post_comment(pull_ref, cleaned))
        except Exception as error:
            from review_loop.services.phase_support import pause
            from review_loop.types.run import PauseReason

            run.extra["last_github_error"] = str(error)[:500]
            return pause(deps, run, PauseReason.GITHUB_ERROR, resume_state=run.state)
    if deps.notifier is not None:
        deps.notifier.notify("review-loop", f"PR #{run.pr_number}: {outcome.value.replace('_', ' ')} at {run.head_sha[:9]} after {run.pass_no} passes")
    return save(deps, run, RunState.COMPLETE)


def _loop_commits(deps: Deps, run: Run, events: dict[str, list[dict]]) -> list[dict]:
    """Each fix commit read back from git, so the report shows what was committed rather than what was planned.
    A commit git cannot describe still appears, with no title and files None, meaning unknown."""
    fixes = [event["note"] for items in events.values() for event in items if event["note"].get("commit")]
    commits = []
    for pass_no in range(1, run.pass_no + 1):
        sha = run.extra.get(f"fix_commit_pass_{pass_no}")
        if not sha:
            continue
        try:
            parent, message = deps.git.commit_info(run.worktree_path, sha)
            files = deps.git.changed_files(run.worktree_path, parent, sha) if parent else None
        except RuntimeError:
            message, files = "", None
        pushed = any(note["commit"] == sha and note.get("pushed", True) for note in fixes)
        commits.append({"sha": sha, "pass_no": pass_no, "title": message.strip().splitlines()[0] if message.strip() else "",
                        "files": files, "pushed": pushed})
    return commits
