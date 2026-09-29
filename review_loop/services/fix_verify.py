"""Phase D fixes exactly the accepted findings; phase E verifies, commits without trailers, and pushes with a lease."""

from __future__ import annotations

import json
from dataclasses import dataclass

from review_loop.engine.findings import FindingState
from review_loop.engine.packet import PacketInput, render_packet
from review_loop.engine.prompts import fill_template
from review_loop.engine.scope import scope_drift
from review_loop.engine.trailers import forbidden_trailer_lines, strip_forbidden_trailers
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import phases as phases_repo
from review_loop.repositories import runs as runs_repo
from review_loop.repositories import verification as verification_repo
from review_loop.services.phase_support import (
    Deps, apply_events, author_resume, decisions, events_by_finding, instruction_files, keep_author_session, now, pass_dir, pause,
    project_env, pull_values, ref, remote_head, repo, request, run_agent, save, template, transaction, verification_lines,
)
from review_loop.services.pytest_checks import pytest_check_env
from review_loop.services.workspace import registered_script_files
from review_loop.types.result import Err, Ok, Result
from review_loop.types.run import PauseReason, Run, RunState

FIX_ACTIONS_TEXT = "Edit code and tests for the accepted findings only. Do not commit, push, or run gh; the coordinator does that."


def phase_fix(deps: Deps, run: Run) -> Run:
    repo_config = repo(deps, run)
    directory = pass_dir(deps, run, run.pass_no)
    repairing = bool(run.extra.get(f"verify_failure_pass_{run.pass_no}"))
    if not repairing and not deps.git.is_clean(run.worktree_path):
        return pause(deps, run, PauseReason.WORKSPACE_DIRTY)
    accepted = [f for f in findings_repo.list_findings(deps.conn, run.id) if f.state == FindingState.ACCEPTED]
    prompt = _fix_prompt(deps, run, repo_config, directory, accepted)
    attempt = phases_repo.count_attempts(deps.conn, run.id, "fix", run.pass_no) + 1
    phase_request = request(deps, repo_config, run, "fix", prompt, "fix", directory / f"fix-{attempt}", "write",
                            repo_config.review.author_model, repo_config.review.author_effort, resume=author_resume(run, repo_config))
    outcome = run_agent(deps, run, repo_config.review.author, phase_request, run.pass_no, state=RunState.FIXING)
    if not outcome.ok:
        return pause(deps, run, outcome.error)
    keep_author_session(run, repo_config, outcome.value.session_id)
    _record_fix_output(deps, run, outcome.value.data, accepted)
    return save(deps, run, RunState.VERIFYING)


def _fix_prompt(deps: Deps, run: Run, repo_config, directory, accepted) -> str:
    pull = deps.github.fetch_pull(ref(run))
    packet = render_packet(PacketInput(
        pull=pull, merge_base_sha=run.merge_base_sha, instruction_files=instruction_files(repo_config, run),
        discussion_text=(directory / "discussion.md").read_text() if (directory / "discussion.md").exists() else "",
        findings=findings_repo.list_findings(deps.conn, run.id), events=events_by_finding(deps, run), decisions=decisions(deps, run),
        verification=verification_lines(deps, run), phase="fix", active_ids=[], since_last_review=[],
        permitted_actions=FIX_ACTIONS_TEXT, accepted_ids=[f.id for f in accepted],
    ))
    failure = run.extra.get(f"verify_failure_pass_{run.pass_no}", "")
    if failure:
        packet += ("\n## Verification failure\n\nThe required checks failed on the tree that holds your changes. Repair whatever "
                   "makes them fail, whether your change or code the PR already had: that repair is inside your scope even when no "
                   "finding names the file. List each repaired file in the change entry of the finding it unblocks and say why.\n\n"
                   f"```\n{failure}\n```\n")
    (directory / "packet-fix.md").write_text(packet)
    return fill_template(template(deps, "fix"), {
        **pull_values(pull, run), "accepted_findings": _accepted_lines(accepted), "contract": run.extra.get("contract", "(not stated)"),
        "test_hint": " ".join(repo_config.verification.required[0]) if repo_config.verification.required else "the project's targeted tests",
    })


def _record_fix_output(deps: Deps, run: Run, fix: dict, accepted) -> None:
    """Commit text for phase E, pending rejections for what the author left unchanged, and the drift list."""
    run.extra["commit_title"], run.extra["commit_body"] = fix["commit_title"], fix["commit_body"]
    run.extra[f"fix_pass_{run.pass_no}"] = json.dumps({"changes": [c["finding_id"] for c in fix["changes"]], "tests_run": fix["tests_run"]})
    at = now(deps)
    changed_ids = {change["finding_id"] for change in fix["changes"]} | set(run.extra.get(f"changed_ids_pass_{run.pass_no}", []))
    run.extra[f"changed_ids_pass_{run.pass_no}"] = sorted(changed_ids)
    declined = {entry["finding_id"]: entry["reason"] for entry in fix.get("not_changed", [])}
    for finding in accepted:
        if finding.id in changed_ids and finding.id not in declined:
            continue
        reason = declined.get(finding.id, "no change reported for this finding by the fix phase")
        apply_events(deps, run, finding, ("unfixed",), "author",
                     {"disposition": "unfixed", "reply": reason, "evidence": "", "intended_fix": "", "pass": run.pass_no}, at)
    declared = [path for change in fix["changes"] for path in change["files"]]
    run.extra[f"drift_pass_{run.pass_no}"] = scope_drift(deps.git.working_changed_files(run.worktree_path), [f.file for f in accepted], declared)


def phase_verify(deps: Deps, run: Run) -> Run:
    repo_config = repo(deps, run)
    directory = pass_dir(deps, run, run.pass_no)
    touched_scripts = set(deps.git.working_changed_files(run.worktree_path)) & registered_script_files(repo_config)
    if touched_scripts:
        run.extra["scripts_changed"] = sorted(touched_scripts)
        return pause(deps, run, PauseReason.SCRIPTS_CHANGED, resume_state=RunState.VERIFYING)
    attempt_no = len([r for r in verification_repo.list_results(deps.conn, run.id) if r["pass_no"] == run.pass_no]) + 1
    checks = _run_checks(deps, run, repo_config, project_env(deps, repo_config))
    log_path = directory / f"verify-{attempt_no}.log"
    log_path.write_text(checks.log)
    verification_repo.add_result(deps.conn, run.id, run.pass_no, attempt_no, checks.tree, checks.commands, checks.status, str(log_path), now(deps))
    if checks.status == "tree_changed":
        return pause(deps, run, PauseReason.UNEXPECTED_COMMIT, resume_state=RunState.VERIFYING)
    if checks.status == "unavailable":
        return pause(deps, run, PauseReason.CHECKS_FAILED, resume_state=RunState.VERIFYING)
    if checks.status == "failed":
        # The budget is fix runs, so a verification that ran nothing never uses up the repair a later failure is owed.
        if phases_repo.count_attempts(deps.conn, run.id, "fix", run.pass_no) < run.budgets.max_fix_attempts:
            run.extra[f"verify_failure_pass_{run.pass_no}"] = checks.log[-4000:]
            return save(deps, run, RunState.FIXING)
        return pause(deps, run, PauseReason.CHECKS_FAILED, resume_state=RunState.VERIFYING)
    return _commit_and_push(deps, run, repo_config)


def _commit_and_push(deps: Deps, run: Run, repo_config) -> Run:
    pull = deps.github.fetch_pull(ref(run))
    if pull.state != "open":
        return save(deps, run, RunState.CANCELLED)
    candidate = _commit_if_needed(deps, run, repo_config)
    if not candidate.ok:
        return pause(deps, run, candidate.error, resume_state=RunState.VERIFYING)
    if candidate.value is None:
        return _publish_without_changes(deps, run)
    sha = candidate.value
    run.extra[f"candidate_commit_pass_{run.pass_no}"] = sha
    # Git says where the branch points. The candidate already there is this run's own push, recorded or not: a pause or
    # a crash can follow a successful push before the record is written, and the API can still be answering from before it.
    remote_sha = deps.git.remote_branch_head(run.worktree_path, repo_config.remote, run.head_ref)
    if pull.head_sha == sha or remote_sha == sha:
        return _record_pushed(deps, run, sha)
    if remote_sha != remote_head(run):
        return pause(deps, run, PauseReason.HEAD_CHANGED, resume_state=RunState.VERIFYING)
    if not repo_config.publication.push_verified_fixes:
        run.extra["unpushed_commits"] = run.extra.get("unpushed_commits", []) + [sha]
        return _record_pushed(deps, run, sha, pushed=False)
    # The push is reserved by writing the coordinator's own record in one statement that fails once a person's control
    # has landed; a control that lands after it finds the push in flight. The verified commit stays local when refused.
    run.updated_at = now(deps)
    if not runs_repo.save_run_unless_controlled(deps.conn, run):
        return runs_repo.get_run(deps.conn, run.id) or run
    pushed = deps.git.push_guarded(run.worktree_path, repo_config.remote, sha, run.head_ref, remote_head(run))
    if not pushed.ok:
        reason = PauseReason.HEAD_CHANGED if pushed.error == "head_changed" else PauseReason.PUSH_FAILED
        return pause(deps, run, reason, resume_state=RunState.VERIFYING)
    return _record_pushed(deps, run, sha)


def _record_pushed(deps: Deps, run: Run, sha: str, pushed: bool = True) -> Run:
    """What happened on the branch is progress, written with the findings it fixed in one transaction: a crash leaves
    either both or neither, never a head that reads as "no change" beside findings still waiting for one. The progress
    lands whatever a person did meanwhile; only the transition is theirs to refuse."""
    run.head_sha = sha
    run.extra["verified_head"] = sha
    if pushed:
        run.extra["remote_head"] = sha
    run.extra[f"fix_commit_pass_{run.pass_no}"] = sha
    at = now(deps)
    with transaction(deps.conn):
        for finding in findings_repo.list_findings(deps.conn, run.id):
            if finding.state == FindingState.ACCEPTED:
                apply_events(deps, run, finding, ("fixed",), "coordinator", {"commit": sha, "pass": run.pass_no, "pushed": pushed}, at)
        run.updated_at = at
        runs_repo.save_run_keeping_control(deps.conn, run)
    return save(deps, run, RunState.PUBLISHING)


@dataclass(frozen=True)
class CheckOutcome:
    """`commands` is what actually ran, so the record never claims a check that a failure before it skipped."""

    status: str
    log: str
    tree: str
    commands: list[list[str]]


def _run_checks(deps: Deps, run: Run, repo_config, env: dict[str, str]) -> CheckOutcome:
    """Formatting first, then the tree is hashed, the required checks run, and the tree is hashed again: the
    checked tree is the one that gets committed. No required check run means unavailable, never passed. A required
    check that selected nothing hands over to the fallback, whose result then stands in its place."""
    verification = repo_config.verification
    timeout = repo_config.review.timeouts_minutes.get("verify", 60) * 60
    log_parts: list[str] = []
    ran: list[list[str]] = []
    for command in verification.format:
        ran.append(command)
        if _outcome(deps.process.run(command, cwd=run.worktree_path, env=env, timeout_seconds=timeout), verification, log_parts) != "passed":
            return CheckOutcome("failed", "\n".join(log_parts), "", ran)
    tree_before = deps.git.stage_all_and_tree_hash(run.worktree_path)
    statuses = _run_required(deps, run, verification.required, verification, env, timeout, log_parts, ran)
    if _selected_nothing(statuses) and verification.fallback:
        statuses = _run_required(deps, run, verification.fallback, verification, env, timeout, log_parts, ran)
    tree_after = deps.git.stage_all_and_tree_hash(run.worktree_path)
    log = "\n".join(log_parts)
    if tree_after != tree_before:
        return CheckOutcome("tree_changed", log + "\nthe working tree changed while the checks ran", tree_after, ran)
    if "failed" in statuses:
        return CheckOutcome("failed", log, tree_after, ran)
    if not statuses or any(status != "passed" for status in statuses):
        return CheckOutcome("unavailable", log or "no required checks are registered for this repository", tree_after, ran)
    run.extra["verified_tree"] = tree_after
    return CheckOutcome("passed", log, tree_after, ran)


def _run_required(deps: Deps, run: Run, commands: list[list[str]], verification, env: dict[str, str], timeout: int,
                  log_parts: list[str], ran: list[list[str]]) -> list[str]:
    ran.extend(commands)
    return [_outcome(deps.process.run(command, cwd=run.worktree_path, env=pytest_check_env(command, run.worktree_path, env),
                                      timeout_seconds=timeout), verification, log_parts)
            for command in commands]


def _selected_nothing(statuses: list[str]) -> bool:
    """Nothing failed or timed out, and at least one check said the change maps to no test it knows."""
    return "nothing_selected" in statuses and all(status in ("passed", "nothing_selected") for status in statuses)


def _outcome(completed, verification, log_parts: list[str]) -> str:
    log_parts.append(f"$ {' '.join(completed.argv)}\nexit {completed.exit_code}{' (timed out)' if completed.timed_out else ''}\n{completed.stdout}\n{completed.stderr}")
    if completed.timed_out:
        return "unavailable"
    if completed.exit_code == 0:
        return "passed"
    if completed.exit_code in verification.unavailable_exit_codes:
        return "nothing_selected"
    return "failed"


def _commit_if_needed(deps: Deps, run: Run, repo_config) -> Result[str | None, PauseReason]:
    """Ok(sha) for the commit to push, Ok(None) when the fix changed nothing, Err when the tree holds a commit that is not ours."""
    local_head = deps.git.head_sha(run.worktree_path)
    dirty = bool(deps.git.working_changed_files(run.worktree_path))
    if not dirty and local_head == run.head_sha:
        return Ok(None)
    forbidden = repo_config.publication.forbid_commit_trailers
    if not dirty:
        parent, message = deps.git.commit_info(run.worktree_path, local_head)
        if parent == run.head_sha and _pass_line(run) in message and not forbidden_trailer_lines(message, forbidden):
            return Ok(local_head)
        return Err(PauseReason.UNEXPECTED_COMMIT)
    fixed_ids = [f.id for f in findings_repo.list_findings(deps.conn, run.id) if f.state == FindingState.ACCEPTED]
    message = f"{run.extra.get('commit_title', 'fix: address review findings')}\n\n{run.extra.get('commit_body', '').strip()}\n\n" \
              f"{_pass_line(run)}: {', '.join(fixed_ids)}\n"
    removed = forbidden_trailer_lines(message, forbidden)
    if removed:
        run.extra[f"stripped_trailers_pass_{run.pass_no}"] = removed
    sha = deps.git.commit(run.worktree_path, strip_forbidden_trailers(message, forbidden))
    parent, made = deps.git.commit_info(run.worktree_path, sha)
    if parent != run.head_sha or forbidden_trailer_lines(made, forbidden):
        return Err(PauseReason.UNEXPECTED_COMMIT)
    if run.extra.get("verified_tree") and deps.git.commit_tree(run.worktree_path, sha) != run.extra["verified_tree"]:
        return Err(PauseReason.UNEXPECTED_COMMIT)
    return Ok(sha)


def _pass_line(run: Run) -> str:
    return f"Review response, pass {run.pass_no}"


def _publish_without_changes(deps: Deps, run: Run) -> Run:
    """Nothing to commit: the head is now verified; whatever was still accepted becomes a pending rejection,
    and a pass with nothing to publish goes straight back to the completion decision."""
    at = now(deps)
    run.extra["verified_head"] = run.head_sha
    for finding in findings_repo.list_findings(deps.conn, run.id):
        if finding.state == FindingState.ACCEPTED:
            apply_events(deps, run, finding, ("unfixed",), "author",
                         {"disposition": "unfixed", "reply": "the fix phase changed no files", "evidence": "", "intended_fix": "", "pass": run.pass_no}, at)
    has_replies = any(e["actor"] == "author" and e["note"].get("pass") == run.pass_no and e["note"].get("reply") is not None
                      for e in findings_repo.list_events(deps.conn, run.id))
    if has_replies:
        return save(deps, run, RunState.PUBLISHING)
    from review_loop.services.run_coordinator import _after_review

    return _after_review(deps, run)


def _accepted_lines(accepted) -> str:
    return "\n".join(
        f"- {f.id} [{f.severity}] {f.title}\n  Location: {f.file}:{f.line}\n  Protected behaviour: {f.protected_behaviour}\n"
        f"  Intended fix: {_intended_fix(f)}" for f in accepted) or "(none)"


def _intended_fix(finding) -> str:
    return finding.extra.get("intended_fix", "") if finding.extra else ""
