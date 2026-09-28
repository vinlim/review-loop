"""Phase G: the alignment exchange, then blind arbitration, then the severity rule; no developer in the loop."""

from __future__ import annotations

from review_loop.engine.arbitration import arbitration_values, combine_verdicts, map_choice
from review_loop.engine.assessment import validate_dispositions
from review_loop.engine.discussion import Role, make_marker
from review_loop.engine.findings import FindingState, Severity
from review_loop.engine.packet import PacketInput, render_packet
from review_loop.engine.prompts import fill_template
from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import outbox as outbox_repo
from review_loop.services.completion_phase import complete_run
from review_loop.services.phase_support import (
    ASSESS_ACTIONS_TEXT, Deps, alignment_block, apply_events, decisions, events_by_finding, inspect_only, instruction_files, now,
    pass_dir, pause, pull_values, ref, remote_head, repo, request, run_agent, save, template, verification_lines,
)
from review_loop.services.phase_support import publisher as make_publisher
from review_loop.types.findings import Finding
from review_loop.types.run import Outcome, PauseReason, Run, RunState

DISPUTE_STATES = {FindingState.DISPUTED, FindingState.NEEDS_ALIGNMENT, FindingState.OPEN}
DECIDED_SOURCES = {"agreement", "arbitration", "severity_rule", "override"}
APPLY = {"fix": ("decide_fix", "needs_alignment"), "keep": ("decide_keep",), "exception": ("except",)}


def phase_align(deps: Deps, run: Run) -> Run:
    disputed = _disputed_findings(deps, run)
    exhausted = [f for f in disputed if _exchanges_used(deps, run, f) >= run.budgets.max_alignment_exchanges]
    blocked = False
    for finding in exhausted:
        blocked = _apply_severity_rule(deps, run, finding, "the alignment budget for this concern is spent") == "blocked" or blocked
    disputed = [f for f in disputed if f not in exhausted]
    if blocked:
        return complete_run(deps, run, Outcome.BLOCKED, exhausted=False)
    if not disputed:
        return save(deps, run, _next_state(deps, run))
    directory = pass_dir(deps, run, run.pass_no)
    note = _alignment_note(deps, run, disputed, directory)
    if note is None:
        return pause(deps, run, run.pause_reason or _last_pause(deps))
    remaining = _assess_against_note(deps, run, disputed, directory)
    if remaining is None:
        return pause(deps, run, _last_pause(deps))
    blocked = False
    for finding in remaining:
        verdict = _arbitrate(deps, run, finding, directory)
        if verdict is None:
            return pause(deps, run, _last_pause(deps))
        blocked = blocked or verdict == "blocked"
    _post_alignment(deps, run, note, disputed)
    if deps.extra.pop("github_error", False):
        return pause(deps, run, PauseReason.GITHUB_ERROR, resume_state=RunState.ALIGNING)
    if blocked:
        return complete_run(deps, run, Outcome.BLOCKED, exhausted=False)
    return save(deps, run, _next_state(deps, run))


def _disputed_findings(deps: Deps, run: Run) -> list[Finding]:
    flagged = set(run.extra.get("signal_finding_ids", []))
    disputed = []
    for finding in findings_repo.list_findings(deps.conn, run.id):
        state = FindingState(finding.state)
        if state in (FindingState.DISPUTED, FindingState.NEEDS_ALIGNMENT):
            disputed.append(finding)
        elif state == FindingState.OPEN and (finding.id in flagged or _signal_fired(run)) and finding.severity in (Severity.BLOCKER, Severity.ISSUE):
            disputed.append(finding)
    return disputed


def _exchanges_used(deps: Deps, run: Run, finding: Finding) -> int:
    """Decisions already taken on this concern or the concerns it supersedes."""
    chain = {finding.id}
    by_id = {f.id: f for f in findings_repo.list_findings(deps.conn, run.id)}
    current = finding
    while current.supersedes and current.supersedes in by_id and current.supersedes not in chain:
        chain.add(current.supersedes)
        current = by_id[current.supersedes]
    return sum(1 for d in decisions_repo.list_decisions(deps.conn, run.id) if d["source"] in DECIDED_SOURCES and set(d["finding_ids"]) & chain)


def _apply_severity_rule(deps: Deps, run: Run, finding: Finding, reason: str) -> str:
    """No more exchanges: an ISSUE or lower becomes an exception, a BLOCKER stays open and blocks."""
    at = now(deps)
    current = findings_repo.get_finding(deps.conn, run.id, finding.id)
    kind = "blocked" if current.severity == Severity.BLOCKER else "exception"
    decisions_repo.add_decision(deps.conn, run.id, [current.id], "severity_rule", "", f"{kind}: {reason}", {"kind": kind}, at)
    if kind == "exception":
        apply_events(deps, run, current, ("align", "needs_alignment"), "coordinator", {"note": reason, "pass": run.pass_no}, at)
        current = findings_repo.get_finding(deps.conn, run.id, current.id)
        apply_events(deps, run, current, ("except",), "coordinator", {"note": f"exception: {reason}", "pass": run.pass_no}, at)
    return kind


def _signal_fired(run: Run) -> bool:
    return bool(run.extra.get(f"signals_pass_{run.pass_no}"))


def _alignment_note(deps: Deps, run: Run, disputed: list[Finding], directory) -> dict | None:
    repo_config, pull_ref = repo(deps, run), ref(run)
    pull = deps.github.fetch_pull(pull_ref)
    signals = run.extra.get(f"signals_pass_{run.pass_no}", []) or [f"dispute on {f.id}" for f in disputed]
    packet = render_packet(PacketInput(
        pull=pull, merge_base_sha=run.merge_base_sha, instruction_files=instruction_files(repo_config, run),
        discussion_text=(directory / "discussion.md").read_text() if (directory / "discussion.md").exists() else "",
        findings=findings_repo.list_findings(deps.conn, run.id), events=events_by_finding(deps, run), decisions=decisions(deps, run),
        verification=verification_lines(deps, run), phase="align", active_ids=[f.id for f in disputed], since_last_review=[],
        permitted_actions="Read only. Write the alignment note the schema requires.",
    ))
    (directory / "packet-align.md").write_text(packet)
    prompt = fill_template(template(deps, "align"), {**pull_values(pull, run), "packet_path": str(directory / "packet-align.md"),
                                                      "signals": "; ".join(str(s) for s in signals)})
    phase_request = request(deps, repo_config, run, "align", prompt, "alignment", directory / "align", "read-only",
                            repo_config.review.reviewer_model, "high")
    outcome = run_agent(deps, run, deps.reviewer, phase_request, run.pass_no, state=RunState.ALIGNING)
    if not outcome.ok:
        deps.extra["last_pause"] = outcome.error
        return None
    note = outcome.value.data
    decisions_repo.add_decision(deps.conn, run.id, [f.id for f in disputed], "alignment_note", note["note"], note["preferred_resolution"],
                                {"design_gap": note["design_gap"], "contract": note["contract"], "invariant": note["invariant"]}, now(deps))
    at = now(deps)
    for finding in disputed:
        apply_events(deps, run, finding, ("align", "needs_alignment"), "coordinator", {"note": "alignment exchange", "pass": run.pass_no}, at)
    return note


def _assess_against_note(deps: Deps, run: Run, disputed: list[Finding], directory) -> list[Finding] | None:
    """The author reads the note; agreement is a decision, anything else goes to arbitration."""
    repo_config = repo(deps, run)
    ids = [f.id for f in disputed]
    prompt = _note_assessment_prompt(deps, run, repo_config, directory, ids)
    phase_request = request(deps, repo_config, run, "assess", prompt, "assessment", directory / "align-assess", "read-only",
                            repo_config.review.author_model, repo_config.review.author_effort, resume=run.author_session)
    outcome = run_agent(deps, run, deps.author, phase_request, run.pass_no, state=RunState.ALIGNING,
                        validate=lambda data: validate_dispositions(ids, data["dispositions"]))
    if not outcome.ok:
        deps.extra["last_pause"] = outcome.error
        return None
    run.author_session = outcome.value.session_id or run.author_session
    return _apply_note_dispositions(deps, run, disputed, outcome.value.data["dispositions"])


def _note_assessment_prompt(deps: Deps, run: Run, repo_config, directory, ids: list[str]) -> str:
    pull = deps.github.fetch_pull(ref(run))
    packet = render_packet(PacketInput(
        pull=pull, merge_base_sha=run.merge_base_sha, instruction_files=instruction_files(repo_config, run),
        discussion_text=(directory / "discussion.md").read_text() if (directory / "discussion.md").exists() else "",
        findings=findings_repo.list_findings(deps.conn, run.id), events=events_by_finding(deps, run), decisions=decisions(deps, run),
        verification=verification_lines(deps, run), phase="assess", active_ids=ids, since_last_review=[], permitted_actions=ASSESS_ACTIONS_TEXT,
    ))
    (directory / "packet-align-assess.md").write_text(packet)
    return fill_template(template(deps, "assess"), {
        **pull_values(pull, run), "packet_path": str(directory / "packet-align-assess.md"),
        "authorship_line": "This session continues the one that wrote the PR." if run.author_session else "Assess from the code and the packet.",
        "alignment_block": alignment_block(deps, run),
    })


def _apply_note_dispositions(deps: Deps, run: Run, disputed: list[Finding], dispositions: list[dict]) -> list[Finding]:
    at = now(deps)
    remaining = []
    by_id = {f.id: f for f in disputed}
    for disposition in dispositions:
        finding = findings_repo.get_finding(deps.conn, run.id, disposition["finding_id"])
        note = {**{k: disposition.get(k, "") for k in ("disposition", "reply", "evidence", "intended_fix")}, "pass": run.pass_no}
        if disposition["disposition"] == "accept":
            decisions_repo.add_decision(deps.conn, run.id, [finding.id], "agreement", "", "fix: the author accepted after the alignment note",
                                        {"kind": "fix", "reply": disposition["reply"]}, at)
            apply_events(deps, run, finding, ("decide_fix",), "author", note, at)
            continue
        findings_repo.add_event(deps.conn, run.id, finding.id, finding.state, finding.state, "author", note, at)
        remaining.append(by_id[finding.id])
    return remaining


def _arbitrate(deps: Deps, run: Run, finding: Finding, directory) -> str | None:
    choices = _run_arbiters(deps, run, finding, directory)
    if choices is None:
        return None
    verdict = combine_verdicts(choices[0][1], choices[1][1], finding.severity)
    at = now(deps)
    current = findings_repo.get_finding(deps.conn, run.id, finding.id)
    for agent_name, choice, data in choices:
        findings_repo.add_event(deps.conn, run.id, finding.id, current.state, current.state, "arbiter",
                                {"arbiter": agent_name, "decision": choice, "rationale": data["rationale"], "residual": data["residual"], "pass": run.pass_no}, at)
    source = "arbitration" if verdict.kind in ("fix", "keep") else "severity_rule"
    decisions_repo.add_decision(deps.conn, run.id, [finding.id], source, "", f"{verdict.kind}: {verdict.reason}",
                                {"kind": verdict.kind, "verdicts": [(name, choice) for name, choice, _ in choices]}, at)
    if verdict.kind in APPLY:
        apply_events(deps, run, current, APPLY[verdict.kind], "coordinator", {"note": f"{verdict.kind}: {verdict.reason}", "pass": run.pass_no}, at)
    return verdict.kind


def _run_arbiters(deps: Deps, run: Run, finding: Finding, directory) -> list[tuple[str, str, dict]] | None:
    """Codex and Claude each judge under swapped labels; returns (name, fix|keep|neither, output) per arbiter."""
    repo_config = repo(deps, run)
    pull = deps.github.fetch_pull(ref(run))
    reviewer_position, author_position = _positions(deps, run, finding)
    first_values, second_values = arbitration_values(reviewer_position, author_position)
    base_values = {**pull_values(pull, run), "contract": run.extra.get("contract", "(not stated)"),
                   "finding": f"{finding.id} [{finding.severity}] {finding.title} at {finding.file}:{finding.line}. Protected behaviour: {finding.protected_behaviour}"}
    choices = []
    for agent_name, agent, values, model in (("codex", deps.reviewer, first_values, repo_config.review.reviewer_model),
                                             ("claude", deps.author, second_values, repo_config.review.author_model)):
        prompt = fill_template(template(deps, "arbitrate"), {**base_values, "position_a": values["position_a"], "position_b": values["position_b"]})
        phase_request = request(deps, repo_config, run, "arbitrate", prompt, "arbitration", directory / f"arbitrate-{finding.id}-{agent_name}",
                                "read-only", model, "high")
        outcome = run_agent(deps, run, agent, phase_request, run.pass_no, state=RunState.ALIGNING)
        if not outcome.ok:
            deps.extra["last_pause"] = outcome.error
            return None
        choices.append((agent_name, map_choice(outcome.value.data["decision"], values), outcome.value.data))
    return choices


def _positions(deps: Deps, run: Run, finding: Finding) -> tuple[str, str]:
    events = findings_repo.list_events(deps.conn, run.id, finding.id)
    reviewer_notes = [e["note"].get("note", "") for e in events if e["actor"] == "reviewer" and e["note"].get("note")]
    author_replies = [e["note"] for e in events if e["actor"] == "author" and e["note"].get("reply")]
    reviewer_position = f"{finding.finding} Evidence: {finding.evidence}"
    if finding.new_evidence:
        reviewer_position += f" New evidence: {finding.new_evidence}"
    if reviewer_notes:
        reviewer_position += f" Latest note: {reviewer_notes[-1]}"
    author_position = "(no reply recorded)"
    if author_replies:
        latest = author_replies[-1]
        author_position = latest["reply"] + (f" Evidence: {latest['evidence']}" if latest.get("evidence") else "")
    return reviewer_position, author_position


def _post_alignment(deps: Deps, run: Run, note: dict, disputed: list[Finding]) -> None:
    if inspect_only(deps, run) or not repo(deps, run).publication.post_reviews:
        return
    pull = deps.github.fetch_pull(ref(run))
    if pull.state != "open" or pull.head_sha != remote_head(run):
        return
    publisher = make_publisher(deps, run)
    publisher.reconcile(run.id, ref(run))
    marker = make_marker(Role.REVIEWER, run.id, run.pass_no, "alignment")
    if outbox_repo.has_done(deps.conn, run.id, marker):
        return
    decided = [d for d in decisions_repo.list_decisions(deps.conn, run.id) if d["source"] in DECIDED_SOURCES]
    lines = [f"## Alignment note (pass {run.pass_no})", "", note["note"].strip(), "", "Disputed: " + ", ".join(f.id for f in disputed), ""]
    lines += [f"Decision v{d['version']} ({d['source']}, {', '.join(d['finding_ids'])}): {d['decision']}" for d in decided[-len(disputed):]]
    lines += ["", marker]
    body = "\n".join(lines) + "\n"
    pull_ref = ref(run)
    try:
        publisher.post(run.id, "alignment", {"pass": run.pass_no}, marker, body, lambda cleaned: deps.github.post_comment(pull_ref, cleaned))
    except Exception:
        deps.extra["github_error"] = True


def _next_state(deps: Deps, run: Run) -> RunState:
    states = {f.state for f in findings_repo.list_findings(deps.conn, run.id)}
    return RunState.FIXING if FindingState.ACCEPTED in states else RunState.PUBLISHING


def _last_pause(deps: Deps):
    from review_loop.types.run import PauseReason

    return deps.extra.get("last_pause", PauseReason.AGENT_FAILED)
