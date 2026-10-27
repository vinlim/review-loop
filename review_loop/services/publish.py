"""Author replies in their threads, one round summary, and the inbox mirror, each posted once."""

from __future__ import annotations

from review_loop.engine.discussion import Role, make_marker
from review_loop.engine.findings import FindingState
from review_loop.engine.redaction import shown_command
from review_loop.engine.reply_render import DECISION_SUMMARY, FIXING, render_inbox_mirror, render_reply, render_summary
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import inbox as inbox_repo
from review_loop.repositories import outbox as outbox_repo
from review_loop.repositories import verification as verification_repo
from review_loop.services.phase_support import Deps, check_pull_before_effect, inspect_only, pause, publisher as make_publisher, ref, repo, save
from review_loop.services.publication import ControlLanded, Publisher
from review_loop.types.run import PauseReason, Run, RunState

DECISION_KINDS = {FindingState.ACCEPTED: "fix", FindingState.REJECTION_ACCEPTED: "keep", FindingState.DEFERRED_BY_DECISION: "exception"}
DECIDERS = {"human": "developer", "coordinator": "alignment"}


def phase_publish(deps: Deps, run: Run) -> Run:
    repo_config, pull_ref = repo(deps, run), ref(run)
    if inspect_only(deps, run) or not repo_config.publication.post_author_responses:
        return save(deps, run, RunState.REREVIEWING)
    stopped = check_pull_before_effect(deps, run)
    if stopped is not None:
        return stopped
    publisher = make_publisher(deps, run)
    try:
        publisher.reconcile(run.id, pull_ref)
        verification = _verification_text(deps, run)
        rows, threadless = _publish_replies(deps, run, publisher, pull_ref, verification)
        summary_marker = make_marker(Role.AUTHOR, run.id, run.pass_no, "summary")
        if not outbox_repo.has_done(deps.conn, run.id, summary_marker):
            new_items = [item for item in _inbox_items(deps, run) if item["source_run"] == run.id and item["source_commit"] == run.extra.get("last_reviewed_sha", run.head_sha)]
            body = render_summary(run.pr_number, run.pass_no, run.head_sha, run.extra.get(f"assess_summary_pass_{run.pass_no}", ""), rows,
                                  verification, run.extra.get(f"drift_pass_{run.pass_no}", []), len(new_items), threadless, summary_marker)
            publisher.post(run.id, "summary", {"pass": run.pass_no}, summary_marker, body, lambda cleaned: deps.github.post_comment(pull_ref, cleaned))
        if repo_config.publication.mirror_inbox_in_pr_comment:
            _mirror_inbox(deps, run, publisher, pull_ref)
    except ControlLanded as landed:
        return landed.run  # what was posted stays posted; the rest waits for a resume, each post idempotent by marker
    except Exception as error:
        run.extra["last_github_error"] = str(error)[:500]
        return pause(deps, run, PauseReason.GITHUB_ERROR, resume_state=RunState.PUBLISHING)
    return save(deps, run, RunState.REREVIEWING)


def _publish_replies(deps: Deps, run: Run, publisher: Publisher, pull_ref, verification: str) -> tuple[list[dict], list[str]]:
    """One reply per dispositioned finding this pass, in its thread when it has one; the rest go into the summary."""
    rows, threadless = [], []
    for finding in findings_repo.list_findings(deps.conn, run.id):
        note = _reply_note(deps, run, finding.id)
        if note is None:
            continue
        # A decision can follow the author's reply in the same pass, after that reply was posted, so it has its own marker.
        kind = "decision" if note["disposition"] in DECISION_SUMMARY else "reply"
        marker = make_marker(Role.AUTHOR, run.id, run.pass_no, kind, finding.id)
        commit = _fix_commit(deps, run, finding.id) if note["disposition"] in FIXING else ""
        rows.append({"id": finding.id, "disposition": note["disposition"], "commit": commit[:9]})
        body = render_reply(finding, note, commit, verification if commit else "", marker)
        if finding.comment_id is None and kind == "decision":
            # The summary may already be posted, so a decision on a finding without a thread is a comment of its own.
            if not outbox_repo.has_done(deps.conn, run.id, marker):
                publisher.post(run.id, "reply", {"finding": finding.id}, marker, f"**{finding.id}**: {body}",
                               lambda cleaned: deps.github.post_comment(pull_ref, cleaned))
        elif finding.comment_id is None:
            threadless.append(f"**{finding.id}**: {body.replace(marker, '').strip()}")
        elif not outbox_repo.has_done(deps.conn, run.id, marker):
            publisher.post(run.id, "reply", {"finding": finding.id}, marker, body,
                           lambda cleaned, cid=finding.comment_id: deps.github.reply_to_comment(pull_ref, cid, cleaned))
    return rows, threadless


def _reply_note(deps: Deps, run: Run, finding_id: str) -> dict | None:
    """The latest word on the finding this pass: the author's reply, or a decision that settled it after that reply."""
    # An author disposition that arrived after a decision is recorded as ignored, and the decision stands.
    events = [e for e in findings_repo.list_events(deps.conn, run.id, finding_id) if e["note"].get("pass") == run.pass_no
              and ((e["actor"] == "author" and e["note"].get("reply") is not None and not e["note"].get("ignored")) or _decided(e))]
    if not events:
        return None
    latest = events[-1]
    if latest["actor"] == "author":
        return latest["note"]
    return {"disposition": f"{DECIDERS[latest['actor']]}_{DECISION_KINDS[latest['to_state']]}", "reply": _decision_reason(latest)}


def _decided(event: dict) -> bool:
    return event["actor"] in DECIDERS and event["from_state"] != event["to_state"] and event["to_state"] in DECISION_KINDS


def _decision_reason(event: dict) -> str:
    """The reason without the verdict the disposition already names: a decision file's first line, or an alignment note's "fix: "."""
    note = event["note"].get("note", "")
    if event["actor"] == "human":
        return note.partition("\n")[2]
    reason = note.removeprefix(f"{DECISION_KINDS[event['to_state']]}: ").strip()
    return f"{reason[:1].upper()}{reason[1:].removesuffix('.')}." if reason else ""


def _fix_commit(deps: Deps, run: Run, finding_id: str) -> str:
    """The commit that fixed this finding in this pass; a fix decided after verification has none yet."""
    fixed = [e for e in findings_repo.list_events(deps.conn, run.id, finding_id) if e["note"].get("pass") == run.pass_no
             and e["from_state"] == FindingState.ACCEPTED and e["to_state"] == FindingState.FIXED_PENDING_VERIFICATION]
    return fixed[-1]["note"].get("commit", "") if fixed else ""


def _verification_text(deps: Deps, run: Run) -> str:
    results = [r for r in verification_repo.list_results(deps.conn, run.id) if r["pass_no"] == run.pass_no]
    if not results:
        return ""
    latest = results[-1]
    checkout = str(repo(deps, run).local_path)
    return f"{latest['status']} ({', '.join(shown_command(c, checkout) for c in latest['commands'])})"


def _inbox_items(deps: Deps, run: Run) -> list[dict]:
    return [item for item in inbox_repo.list_items(deps.conn, repo=run.repo) if item["source_pr"] == run.pr_number]


def _mirror_inbox(deps: Deps, run: Run, publisher: Publisher, pull_ref) -> None:
    items = _inbox_items(deps, run)
    if not items:
        return
    marker = make_marker(Role.AUTHOR, run.id, run.pass_no, "inbox")
    existing = run.extra.get("inbox_comment_id")
    if not existing:
        earlier = outbox_repo.find_done(deps.conn, run.id, "inbox")
        if earlier is not None:
            existing = earlier["receipt"].get("id")
            run.extra["inbox_comment_id"] = existing
    if outbox_repo.has_done(deps.conn, run.id, marker):
        return
    body = render_inbox_mirror(items, marker)
    if existing:
        publisher.post(run.id, "edit_comment", {"comment_id": existing}, marker, body, lambda cleaned: deps.github.edit_comment(pull_ref, existing, cleaned))
        return
    receipt = publisher.post(run.id, "inbox", {"pass": run.pass_no}, marker, body, lambda cleaned: deps.github.post_comment(pull_ref, cleaned))
    run.extra["inbox_comment_id"] = receipt["id"]
