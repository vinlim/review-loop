"""The phase state machine. Each phase is a function from the run to its next persisted state."""

from __future__ import annotations

from review_loop.engine.assessment import validate_dispositions, validate_review_coverage
from review_loop.engine.completion import decide_after_review
from review_loop.engine.discussion import Role, digest, make_marker, render_discussion
from review_loop.engine.findings import FindingState, Severity, assign_ids, fingerprint, is_closed, is_open_required, match_prior
from review_loop.engine.packet import PacketInput, render_packet
from review_loop.engine.placement import diff_lines, place
from review_loop.engine.prompts import fill_template
from review_loop.engine.review_render import render_finding_comment, render_review_body
from review_loop.engine.inbox_match import match_inbox_item
from review_loop.engine.signals import SignalInput, detect_signals
from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import inbox as inbox_repo
from review_loop.repositories import outbox as outbox_repo
from review_loop.services.completion_phase import complete_run
from review_loop.services.phase_support import (
    ASSESS_ACTIONS_TEXT, REVIEW_ACTIONS_TEXT, Deps, adopt_head, alignment_block, apply_events, author_agent, decisions, events_by_finding,
    externally_controlled, finish, inspect_only, instruction_files, now, pass_dir, pause, publisher, pull_values, ref, repo,
    request, reviewer_agent, run_agent, save, template, transaction, trusted_logins, verification_lines,
)
from review_loop.services.workspace import registered_script_files
from review_loop.repositories import runs as runs_repo
from review_loop.services.workspace import WorkspaceProblem, prepare_workspace
from review_loop.types.findings import Finding
from review_loop.types.run import PauseReason, Run, RunState

RESOLUTION_EVENTS = {"verified": ("verify",), "withdrawn": ("withdraw",), "rejection_accepted": ("accept_rejection",),
                     "disputed": ("dispute",)}
SEVERITY_RANK = {"QUESTION": 0, "CHORE": 1, "ISSUE": 2, "BLOCKER": 3}
OMISSION_EVENTS = {FindingState.FIXED_PENDING_VERIFICATION: ("verify",), FindingState.REJECTED_PENDING_REVIEW: ("accept_rejection",)}
DISPOSITION_EVENTS = {"accept": "accept", "reject": "reject", "needs_alignment": "needs_alignment", "answer": "answer"}
STOPPED = {RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED, RunState.PAUSED}
MUTATING = {RunState.FIXING, RunState.VERIFYING}


def run_loop(deps: Deps, run: Run, on_step=None) -> Run:
    while run.state not in STOPPED:
        run = step(deps, run)
        if on_step is not None:
            on_step(run)
    return run


def step(deps: Deps, run: Run) -> Run:
    """One phase. A stop or manual pause recorded by another process is honoured before anything runs,
    and an inspect-only run never enters a phase that edits or pushes."""
    persisted = runs_repo.get_run(deps.conn, run.id)
    if persisted is not None and externally_controlled(persisted):
        return persisted
    if run.state in STOPPED:
        return run
    if inspect_only(deps, run) and run.state in MUTATING:
        return pause(deps, run, PauseReason.INSPECT_ONLY, resume_state=run.state)
    handlers = {RunState.PREPARING: phase_prepare, RunState.REVIEWING: phase_review, RunState.REREVIEWING: phase_review,
                RunState.ASSESSING: phase_assess}
    if run.state in handlers:
        return handlers[run.state](deps, run)
    from review_loop.services import later_phases

    return later_phases.step(deps, run)


# --- phase A: prepare -------------------------------------------------------------------------

def phase_prepare(deps: Deps, run: Run) -> Run:
    repo_config, pull_ref = repo(deps, run), ref(run)
    pull = deps.github.fetch_pull(pull_ref)
    if pull.state != "open":
        return finish(deps, run, RunState.CANCELLED)
    inspect_only(deps, run)
    adopt_head(deps, run, repo_config, pull)
    deps.git.fetch(str(repo_config.local_path), "origin", [run.base_ref, run.head_ref])
    changed = deps.git.changed_files(str(repo_config.local_path), run.merge_base_sha, run.head_sha)
    if set(changed) & registered_script_files(repo_config):
        run.extra["scripts_changed"] = sorted(set(changed) & registered_script_files(repo_config))
        return pause(deps, run, PauseReason.SCRIPTS_CHANGED)
    ready = prepare_workspace(run, repo_config, git=deps.git, process=deps.process, state_dir=deps.settings.state_dir,
                              base_env=deps.base_env, changed_paths=changed)
    if not ready.ok:
        reason = {WorkspaceProblem.DIRTY: PauseReason.WORKSPACE_DIRTY, WorkspaceProblem.FOREIGN: PauseReason.WORKSPACE_FOREIGN}.get(
            ready.error, PauseReason.PREPARE_FAILED)
        return pause(deps, run, reason)
    run.worktree_path, run.local_branch = ready.value.path, ready.value.local_branch
    if run.author_session in ("", "auto"):
        run.author_session = deps.find_author_session(repo_config.review.author, run.head_ref, str(repo_config.local_path))
    return save(deps, run, RunState.REVIEWING)


# --- phase B and F: review and rereview ------------------------------------------------------------

def phase_review(deps: Deps, run: Run) -> Run:
    repo_config, pull_ref = repo(deps, run), ref(run)
    pull = deps.github.fetch_pull(pull_ref)
    if pull.state != "open":
        return finish(deps, run, RunState.CANCELLED)
    if run.extra.get("pending_review_pass") == run.pass_no:
        return _publish_pending_review(deps, run)
    pass_no = run.pass_no + 1
    directory = pass_dir(deps, run, pass_no)
    findings = findings_repo.list_findings(deps.conn, run.id)
    discussion = deps.github.fetch_discussion(pull_ref)
    _record_thread_ids(deps, run, findings, discussion.threads)
    run.extra["discussion_digest"] = digest(discussion)
    diff_text = _write_review_inputs(deps, run, pull, pass_no, directory, findings,
                                     render_discussion(discussion, run.pr_author, trusted_logins(deps, run)))
    phase_request = request(deps, repo_config, run, "review", _review_prompt(deps, run, pull, pass_no, directory), "review",
                            directory / "review", "read-only", repo_config.review.reviewer_model, repo_config.review.reviewer_effort)
    pending_blockers = [f.id for f in findings if f.severity == Severity.BLOCKER
                        and FindingState(f.state) in OMISSION_EVENTS]
    outcome = run_agent(deps, run, reviewer_agent(deps, repo_config), phase_request, pass_no, state=run.state,
                        validate=lambda data: validate_review_coverage(data, pending_blockers))
    if not outcome.ok:
        return pause(deps, run, outcome.error)
    review = outcome.value.data
    with transaction(deps.conn):
        _persist_review(deps, run, review, findings, pass_no, outcome.value.result_path, diff_text)
    return _publish_pending_review(deps, run)


def _persist_review(deps: Deps, run: Run, review: dict, findings: list[Finding], pass_no: int, result_path: str, diff_text: str) -> None:
    """Everything the review changed, in one transaction, before anything is posted: a crash leaves either all or nothing."""
    run.pass_no = pass_no
    if pass_no == 1:
        run.extra["contract"] = f"Owns: {review['contract']['owns']} Does not own: {review['contract']['does_not_own']}"
    findings_repo.delete_pass(deps.conn, run.id, pass_no)
    ids = _store_new_findings(deps, run, review, findings)
    _apply_resolved_prior(deps, run, review, findings)
    run.extra["last_reviewed_sha"] = run.head_sha
    run.extra[f"verdict_pass_{pass_no}"] = review["verdict"]
    stored = pass_dir(deps, run, pass_no) / "review-output.json"
    stored.write_text(__import__("json").dumps(review))
    run.extra["pending_review_pass"] = pass_no
    run.extra["pending_review_path"] = str(stored)
    run.extra["pending_review_ids"] = ids
    run.extra["pending_review_diff"] = str(pass_dir(deps, run, pass_no) / "diff.patch")
    run.updated_at = now(deps)
    runs_repo.save_run(deps.conn, run)


def _publish_pending_review(deps: Deps, run: Run) -> Run:
    """Post the persisted review (once, by marker), write review.md, then decide what comes next."""
    import json

    review = json.loads(open(run.extra["pending_review_path"]).read())
    ids = list(run.extra.get("pending_review_ids", []))
    diff_text = open(run.extra["pending_review_diff"]).read()
    directory = pass_dir(deps, run, run.pass_no)
    _publish_review(deps, run, review, ids, diff_text)
    _resolve_closed_threads(deps, run)
    (directory / "review.md").write_text(render_review_body(review, list(zip(ids, review["findings"])),
                                                            make_marker(Role.REVIEWER, run.id, run.pass_no, "review")))
    for key in ("pending_review_pass", "pending_review_path", "pending_review_ids", "pending_review_diff"):
        run.extra.pop(key, None)
    return _after_review(deps, run)


def _write_review_inputs(deps: Deps, run: Run, pull, pass_no: int, directory, findings: list[Finding], discussion_text: str) -> str:
    """The packet, the full diff and, after pass 1, the delta since the last reviewed commit; returns the full diff."""
    repo_config = repo(deps, run)
    first = pass_no == 1
    last_reviewed = run.extra.get("last_reviewed_sha", run.merge_base_sha)
    (directory / "discussion.md").write_text(discussion_text)
    packet = render_packet(PacketInput(
        pull=pull, merge_base_sha=run.merge_base_sha, instruction_files=instruction_files(repo_config, run),
        discussion_text=discussion_text, findings=findings, events=events_by_finding(deps, run), decisions=decisions(deps, run),
        verification=verification_lines(deps, run), phase="review" if first else "rereview", active_ids=[],
        since_last_review=[] if first else deps.git.log_between(run.worktree_path, last_reviewed, run.head_sha),
        permitted_actions=REVIEW_ACTIONS_TEXT,
    ))
    (directory / "packet.md").write_text(packet)
    diff_text = deps.git.diff(run.worktree_path, run.merge_base_sha, run.head_sha)
    (directory / "diff.patch").write_text(diff_text)
    if not first:
        (directory / "delta.patch").write_text(deps.git.diff(run.worktree_path, last_reviewed, run.head_sha))
    return diff_text


def _review_prompt(deps: Deps, run: Run, pull, pass_no: int, directory) -> str:
    return fill_template(template(deps, "review-initial" if pass_no == 1 else "review-again"), {
        **pull_values(pull, run), "pass_no": str(pass_no), "packet_path": str(directory / "packet.md"),
        "diff_path": str(directory / "diff.patch"), "delta_diff_path": str(directory / "delta.patch"),
    })


def _record_thread_ids(deps: Deps, run: Run, findings: list[Finding], threads) -> None:
    by_comment = {comment_id: thread.id for thread in threads for comment_id in thread.comment_ids}
    at = now(deps)
    for finding in findings:
        thread_id = by_comment.get(finding.comment_id or -1, "")
        if thread_id and finding.thread_id != thread_id:
            finding.thread_id = thread_id
            findings_repo.save_finding(deps.conn, run.id, finding, at)


def _resolve_closed_threads(deps: Deps, run: Run) -> None:
    """Coordinator-owned threads close once their finding is closed; each thread is resolved once."""
    if inspect_only(deps, run) or not repo(deps, run).publication.post_reviews:
        return
    posting = publisher(deps, run)
    for finding in findings_repo.list_findings(deps.conn, run.id):
        if not finding.thread_id or not is_closed(finding):
            continue
        marker = f"resolve:{finding.id}"
        if outbox_repo.has_done(deps.conn, run.id, marker):
            continue
        posting.perform(run.id, "resolve_thread", {"finding": finding.id, "thread": finding.thread_id}, marker,
                        lambda thread=finding.thread_id: deps.github.resolve_thread(thread))


def _store_new_findings(deps: Deps, run: Run, review: dict, prior: list[Finding]) -> list[str]:
    at = now(deps)
    ids = assign_ids(run.pass_no, len(review["findings"]) + len(review["questions"]))
    entries = [(fid, item, False) for fid, item in zip(ids, review["findings"])]
    entries += [(fid, item, True) for fid, item in zip(ids[len(review["findings"]):], review["questions"])]
    for fid, item, is_question in entries:
        finding = _question_finding(fid, run, item) if is_question else _finding_from_review(fid, run, item)
        finding.supersedes, finding.possible_duplicate_of = match_prior(finding, prior)
        findings_repo.create_finding(deps.conn, run.id, finding, at)
        findings_repo.add_event(deps.conn, run.id, fid, "", FindingState.OPEN, "reviewer", {"pass": run.pass_no}, at)
    return ids[: len(review["findings"])]


def _finding_from_review(fid: str, run: Run, item: dict) -> Finding:
    return Finding(id=fid, pass_no=run.pass_no, severity=item["severity"], state=FindingState.OPEN, file=item["file"], line=int(item["line"]),
                   symbol=item["symbol"], title=item["title"], finding=item["finding"], protected_behaviour=item["protected_behaviour"],
                   evidence=item["evidence"], recommendation=item["recommendation"], proposed_refactor=item["proposed_refactor"],
                   fingerprint=fingerprint(item["file"], item["symbol"], item["title"]), supersedes=item["supersedes"],
                   new_evidence=item["new_evidence"])


def _question_finding(fid: str, run: Run, item: dict) -> Finding:
    return Finding(id=fid, pass_no=run.pass_no, severity=Severity.QUESTION, state=FindingState.OPEN, file="", line=0, symbol="",
                   title=item["text"], finding=item["text"], protected_behaviour="", evidence="", recommendation="", proposed_refactor="",
                   fingerprint=fingerprint("", "", item["text"]), blocking=bool(item.get("blocking")))


def _apply_resolved_prior(deps: Deps, run: Run, review: dict, prior: list[Finding]) -> None:
    """Every prior finding gets a resolution: the reviewer's, or by omission (silence after a rereview closes
    an ISSUE or lower; a BLOCKER is never closed by silence, and the review was validated to mention it)."""
    at = now(deps)
    mentioned = set()
    prior_ids = {f.id for f in prior}
    for entry in review.get("resolved_prior", []):
        finding = findings_repo.get_finding(deps.conn, run.id, entry["id"])
        if finding is None or finding.id not in prior_ids:
            continue
        mentioned.add(finding.id)
        resolution, note = entry["resolution"], entry.get("note", "")
        if resolution == "disputed" and not note.strip() and finding.state == FindingState.REJECTED_PENDING_REVIEW:
            resolution, note = "rejection_accepted", "a dispute without new evidence counts as accepting the rejection"
        apply_events(deps, run, finding, RESOLUTION_EVENTS.get(resolution, ()), "reviewer",
                     {"resolution": resolution, "note": note, "pass": run.pass_no}, at)
    if run.pass_no == 1:
        return
    for finding in prior:
        if finding.id in mentioned or FindingState(finding.state) not in OMISSION_EVENTS or finding.severity == Severity.BLOCKER:
            continue
        current = findings_repo.get_finding(deps.conn, run.id, finding.id)
        apply_events(deps, run, current, OMISSION_EVENTS[FindingState(current.state)], "reviewer",
                     {"resolution": "by omission", "note": "by omission", "pass": run.pass_no}, at)


def _publish_review(deps: Deps, run: Run, review: dict, ids: list[str], diff_text: str) -> set[str]:
    """Inline where the diff allows it, in the body otherwise; the posted comment ids stay on the findings."""
    lines = diff_lines(diff_text)
    marker = make_marker(Role.REVIEWER, run.id, run.pass_no, "review")
    inline = []
    for fid, item in zip(ids, review["findings"]):
        if place(item["file"], int(item["line"]), lines) == "inline":
            inline.append({"path": item["file"], "line": int(item["line"]), "body": render_finding_comment(fid, item, make_marker(Role.REVIEWER, run.id, run.pass_no, "finding", fid)), "id": fid})
    inline_ids = {entry["id"] for entry in inline}
    if inspect_only(deps, run) or not repo(deps, run).publication.post_reviews:
        return inline_ids
    pull_ref = ref(run)
    posting = publisher(deps, run)
    posting.reconcile(run.id, pull_ref)
    if outbox_repo.has_done(deps.conn, run.id, marker):
        return inline_ids
    body = render_review_body(review, list(zip(ids, review["findings"])), marker, inline_ids=inline_ids)
    comments = [{k: v for k, v in entry.items() if k != "id"} for entry in inline]
    receipt = posting.post(run.id, "review", {"pass": run.pass_no}, marker, body,
                           lambda cleaned: deps.github.post_review(pull_ref, cleaned, run.head_sha, comments))
    at = now(deps)
    comment_ids = dict(zip([entry["id"] for entry in inline], receipt.get("comment_ids", [])))
    for fid in ids:
        finding = findings_repo.get_finding(deps.conn, run.id, fid)
        if finding is not None:
            finding.review_id = receipt.get("id")
            finding.comment_id = comment_ids.get(fid)
            findings_repo.save_finding(deps.conn, run.id, finding, at)
    return inline_ids


def _after_review(deps: Deps, run: Run) -> Run:
    _apply_standing_decisions(deps, run)
    findings = findings_repo.list_findings(deps.conn, run.id)
    run.extra[f"open_required_pass_{run.pass_no}"] = sum(1 for f in findings if is_open_required(f))
    run.extra[f"head_pass_{run.pass_no}"] = run.head_sha
    decision = decide_after_review(findings, run.pass_no, run.budgets.max_review_passes,
                                   head_verified=run.extra.get("verified_head") == run.head_sha)
    if decision.next == "assess" and _new_signals(deps, run, findings):
        return save(deps, run, RunState.ALIGNING)
    if decision.next == "assess":
        return save(deps, run, RunState.ASSESSING)
    if decision.next == "align":
        return save(deps, run, RunState.ALIGNING)
    if decision.next == "verify":
        return save(deps, run, RunState.VERIFYING)
    return complete_run(deps, run, decision.outcome, exhausted=decision.next == "budget_exhausted")


def _apply_standing_decisions(deps: Deps, run: Run) -> None:
    """A concern re-raised after a decision, with no new evidence, gets the decision again and no model call."""
    kinds = {}
    for decision in decisions_repo.list_decisions(deps.conn, run.id):
        if decision["source"] in ("agreement", "arbitration", "severity_rule", "override"):
            for fid in decision["finding_ids"]:
                kinds[fid] = (decision["rationale"].get("kind", decision["decision"].split(":")[0]), decision["version"])
    at = now(deps)
    by_id = {f.id: f for f in findings_repo.list_findings(deps.conn, run.id)}
    for finding in list(by_id.values()):
        if finding.state != FindingState.OPEN or finding.new_evidence.strip():
            continue
        prior = kinds.get(finding.supersedes)
        decided = by_id.get(finding.supersedes)
        if prior is None or decided is None or SEVERITY_RANK[finding.severity] > SEVERITY_RANK[decided.severity]:
            continue
        kind, version = prior
        if kind == "exception" and finding.severity == Severity.BLOCKER:
            continue
        note = {"note": f"re-raised after decision v{version} without new evidence; the decision stands ({kind})", "re_raised": True, "pass": run.pass_no}
        events = {"keep": ("reject", "accept_rejection"), "fix": ("accept",), "exception": ("needs_alignment", "except")}[kind if kind in ("keep", "fix", "exception") else "keep"]
        for event in events:
            apply_events(deps, run, finding, (event,), "coordinator", note, at)


def _new_signals(deps: Deps, run: Run, findings: list[Finding]) -> bool:
    events: dict[str, list[dict]] = {}
    for event in findings_repo.list_events(deps.conn, run.id):
        events.setdefault(event["finding_id"], []).append(event)
    data = SignalInput(
        findings=findings, events=events,
        open_required_by_pass={n: run.extra[f"open_required_pass_{n}"] for n in range(1, run.pass_no + 1) if f"open_required_pass_{n}" in run.extra},
        blob_hashes=_blob_hashes(deps, run, findings), drift_by_pass={n: run.extra.get(f"drift_pass_{n}", []) for n in range(1, run.pass_no + 1)},
        current_pass=run.pass_no,
    )
    handled = set(run.extra.get("handled_signals", []))
    fresh = [signal for signal in detect_signals(data) if signal.key not in handled]
    if not fresh:
        return False
    run.extra["handled_signals"] = sorted(handled | {signal.key for signal in fresh})
    run.extra[f"signals_pass_{run.pass_no}"] = [f"{s.kind} {s.detail} {' '.join(s.finding_ids)}".strip() for s in fresh]
    run.extra["signal_finding_ids"] = sorted({fid for signal in fresh for fid in signal.finding_ids})
    return True


def _blob_hashes(deps: Deps, run: Run, findings: list[Finding]) -> dict[tuple[int, str], str]:
    hashes = {}
    files = sorted({f.file for f in findings if f.file})
    for n in range(max(1, run.pass_no - 2), run.pass_no + 1):
        sha = run.extra.get(f"head_pass_{n}")
        if not sha:
            continue
        for file in files:
            hashes[(n, file)] = deps.git.file_hash(run.worktree_path, sha, file)
    return hashes


# --- phase C: assess -----------------------------------------------------------------------------

def phase_assess(deps: Deps, run: Run) -> Run:
    repo_config = repo(deps, run)
    directory = pass_dir(deps, run, run.pass_no)
    findings = findings_repo.list_findings(deps.conn, run.id)
    active_ids = [finding.id for finding in findings if finding.state == FindingState.OPEN]
    prompt = _assess_prompt(deps, run, repo_config, directory, findings, active_ids)
    phase_request = request(deps, repo_config, run, "assess", prompt, "assessment", directory / "assess", "read-only",
                            repo_config.review.author_model, repo_config.review.author_effort, resume=run.author_session)
    severities = {f.id: f.severity for f in findings}
    outcome = run_agent(deps, run, author_agent(deps, repo_config), phase_request, run.pass_no, state=RunState.ASSESSING,
                        validate=lambda data: validate_dispositions(active_ids, data["dispositions"], severities))
    if not outcome.ok:
        return pause(deps, run, outcome.error)
    assessment = outcome.value.data
    run.author_session = outcome.value.session_id or run.author_session
    _apply_dispositions(deps, run, assessment)
    _file_adjacent(deps, run, assessment)
    run.extra[f"assess_summary_pass_{run.pass_no}"] = assessment["summary"]
    next_state = _after_assess(deps, run)
    if inspect_only(deps, run):
        return pause(deps, run, PauseReason.INSPECT_ONLY, resume_state=next_state)
    return save(deps, run, next_state)


def _assess_prompt(deps: Deps, run: Run, repo_config, directory, findings: list[Finding], active_ids: list[str]) -> str:
    pull = deps.github.fetch_pull(ref(run))
    discussion_path = directory / "discussion.md"
    packet = render_packet(PacketInput(
        pull=pull, merge_base_sha=run.merge_base_sha, instruction_files=instruction_files(repo_config, run),
        discussion_text=discussion_path.read_text() if discussion_path.exists() else "", findings=findings,
        events=events_by_finding(deps, run), decisions=decisions(deps, run), verification=verification_lines(deps, run),
        phase="assess", active_ids=active_ids, since_last_review=[], permitted_actions=ASSESS_ACTIONS_TEXT,
    ))
    (directory / "packet-assess.md").write_text(packet)
    return fill_template(template(deps, "assess"), {
        **pull_values(pull, run), "packet_path": str(directory / "packet-assess.md"),
        "authorship_line": ("This session continues the one that wrote the PR; still verify against the code, not memory."
                            if run.author_session else "You have no session memory of writing this code; assess from the code and the packet."),
        "alignment_block": alignment_block(deps, run),
    })


def _apply_dispositions(deps: Deps, run: Run, assessment: dict) -> None:
    at = now(deps)
    for disposition in assessment["dispositions"]:
        finding = findings_repo.get_finding(deps.conn, run.id, disposition["finding_id"])
        if finding is None:
            continue
        note = {key: disposition.get(key, "") for key in ("disposition", "reply", "evidence", "intended_fix")}
        apply_events(deps, run, finding, (DISPOSITION_EVENTS[disposition["disposition"]],), "author", {**note, "pass": run.pass_no}, at)


def _file_adjacent(deps: Deps, run: Run, assessment: dict) -> None:
    at = now(deps)
    for item in assessment.get("adjacent_findings", []):
        existing = inbox_repo.list_items(deps.conn, repo=run.repo)
        clear, ambiguous = match_inbox_item(item, existing)
        if clear is not None:
            inbox_repo.add_evidence(deps.conn, clear, f"PR #{run.pr_number} ({run.head_sha[:9]}): {item.get('evidence', '')}", at)
            continue
        new_id = inbox_repo.add_item(deps.conn, run.repo, {**item, "source_pr": run.pr_number, "source_commit": run.head_sha,
                                                           "source_run": run.id, "agent": repo(deps, run).review.author}, at)
        for related in ambiguous:
            inbox_repo.add_related(deps.conn, new_id, related, at)
            inbox_repo.add_related(deps.conn, related, new_id, at)


def _after_assess(deps: Deps, run: Run) -> RunState:
    states = {finding.state for finding in findings_repo.list_findings(deps.conn, run.id)}
    if FindingState.ACCEPTED in states:
        return RunState.FIXING
    if FindingState.NEEDS_ALIGNMENT in states:
        return RunState.ALIGNING
    return RunState.PUBLISHING
