"""Ending a run: the report on disk, the terminal comment on the PR, and a notification."""

from __future__ import annotations

from review_loop.engine.discussion import Role, make_marker
from review_loop.engine.report import render_report
from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import inbox as inbox_repo
from review_loop.repositories import outbox as outbox_repo
from review_loop.repositories import verification as verification_repo
from review_loop.services.phase_support import Deps, inspect_only, publisher as make_publisher, ref, remote_head, save
from review_loop.types.run import Outcome, Run, RunState


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
                           verification_repo.list_results(deps.conn, run.id), inbox_items, exhausted)
    (deps.runs_dir / run.id).mkdir(parents=True, exist_ok=True)
    (deps.runs_dir / run.id / "report.md").write_text(report)
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
                publisher.post(run.id, "final", {"outcome": outcome.value}, marker, report + "\n" + marker,
                               lambda cleaned: deps.github.post_comment(pull_ref, cleaned))
        except Exception as error:
            from review_loop.services.phase_support import pause
            from review_loop.types.run import PauseReason

            run.extra["last_github_error"] = str(error)[:500]
            return pause(deps, run, PauseReason.GITHUB_ERROR, resume_state=run.state)
    if deps.notifier is not None:
        deps.notifier.notify("review-loop", f"PR #{run.pr_number}: {outcome.value.replace('_', ' ')} at {run.head_sha[:9]} after {run.pass_no} passes")
    return save(deps, run, RunState.COMPLETE)
