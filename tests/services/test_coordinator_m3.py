import dataclasses
import json
import os
from pathlib import Path

from review_loop.repositories import findings as findings_repo
from review_loop.repositories import runs as runs_repo
from review_loop.repositories import verification as verification_repo
from review_loop.services.run_coordinator import run_loop, step
from review_loop.types.result import Err
from review_loop.types.run import PauseReason, RunState
from tests.fakes.agent import FakeAgent
from tests.fakes.notifier import FakeNotifier
from tests.services.test_coordinator_phases import APPROVE, ASSESS_984, REVIEW_984, Harness, with_review

LOGGER = "services/notifier/src/logger.ts"
NUMBERS = "app/Support/JsonNumbers.php"
DIFF_984 = f"""diff --git a/{LOGGER} b/{LOGGER}
--- a/{LOGGER}
+++ b/{LOGGER}
@@ -20,10 +20,10 @@
""" + "\n".join(f"+line {n}" for n in range(20, 30)) + f"""
diff --git a/{NUMBERS} b/{NUMBERS}
--- a/{NUMBERS}
+++ b/{NUMBERS}
@@ -155,10 +155,10 @@
""" + "\n".join(f"+line {n}" for n in range(155, 165)) + "\n"

FIX = {
    "changes": [
        {"finding_id": "R1-F1", "files": [LOGGER, "services/notifier/src/logger.test.ts"], "description": "redact once", "tests": ["logger.test.ts"]},
        {"finding_id": "R1-F2", "files": [NUMBERS], "description": "bound bytes", "tests": ["JsonNumbersTest.php"]},
    ],
    "not_changed": [], "commit_title": "fix: redact URLs once and bound the report",
    "commit_body": "Details.\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
    "tests_run": [{"command": "npm test", "result": "189 passed"}],
}
REJECTIONS = {"dispositions": [{"finding_id": fid, "disposition": "reject", "reply": "No: the guard runs first.", "evidence": "X.php:3", "intended_fix": ""}
                               for fid in ("R1-F1", "R1-F2")], "adjacent_findings": [], "summary": "both rejected"}


def verified(*ids):
    return {**APPROVE, "resolved_prior": [{"id": fid, "resolution": "verified", "note": "fix read in the code"} for fid in ids]}


def harness(settings, tmp_path):
    h = Harness(settings, tmp_path)
    h.git.diff_text = DIFF_984
    h.git.working_changed = [LOGGER, "services/notifier/src/logger.test.ts", NUMBERS]
    h.deps.notifier = FakeNotifier()
    h.process.script([".claude/run-tests.sh", "changed"], stdout="Tests: 42 passed")
    return h


def to_fixing(h):
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply(ASSESS_984, session_id="sess-assess")
    return step(h.deps, run)


def to_verifying(h):
    run = to_fixing(h)
    h.author.reply(FIX, session_id="sess-assess")
    return step(h.deps, run)


def to_publishing(h):
    return step(h.deps, to_verifying(h))


# --- fix ------------------------------------------------------------------------------------------

def test_after_a_switch_of_author_agent_the_new_agent_starts_fresh_then_resumes_its_own_session(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    opencode = FakeAgent()
    h.deps.agents["opencode"] = opencode
    h.deps.settings = with_review(h.deps.settings, author="opencode", author_model="", author_effort="")
    opencode.reply(ASSESS_984, session_id="ses-opencode")
    opencode.reply(FIX)

    step(h.deps, step(h.deps, run))

    assess, fix = opencode.requests
    assert assess.resume_session_id == "" and "no session memory" in assess.prompt
    assert fix.resume_session_id == "ses-opencode"


def test_the_fix_request_carries_only_accepted_findings_the_contract_and_write_access(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_fixing(h)
    h.author.reply(FIX)

    run = step(h.deps, run)

    assert run.state == RunState.VERIFYING
    request = h.author.requests[-1]
    assert request.phase == "fix" and request.tools_policy == "write" and request.resume_session_id == "sess-assess"
    packet = (tmp_path / "runs" / run.id / "pass-1" / "packet-fix.md").read_text()
    assert "## Accepted findings to fix" in packet and "R1-F1" in packet and "R1-F2" in packet and "## Active findings" not in packet
    assert REVIEW_984["contract"]["owns"][:40] in request.prompt and ".claude/run-tests.sh changed" in request.prompt
    assert run.extra["commit_title"] == FIX["commit_title"]
    assert run.extra["drift_pass_1"] == []


def test_files_no_finding_names_are_recorded_as_drift(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.git.working_changed.append("app/Unrelated.php")
    run = to_fixing(h)
    h.author.reply(FIX)

    run = step(h.deps, run)

    assert run.extra["drift_pass_1"] == ["app/Unrelated.php"]


def test_a_finding_the_author_leaves_unchanged_becomes_a_pending_rejection(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_fixing(h)
    h.author.reply({**FIX, "changes": FIX["changes"][:1], "not_changed": [{"finding_id": "R1-F2", "reason": "the bound exists upstream"}]})

    run = step(h.deps, run)

    finding = h.findings()["R1-F2"]
    assert finding.state == "rejected_pending_review"
    assert any("upstream" in event["note"].get("reply", "") for event in findings_repo.list_events(h.conn, run.id, "R1-F2"))


# --- verify ---------------------------------------------------------------------------------------

def test_passing_checks_commit_without_trailers_push_with_the_expected_parent_and_move_to_publishing(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_verifying(h)
    old_head = run.head_sha

    run = step(h.deps, run)

    assert run.state == RunState.PUBLISHING
    path, message = h.git.commits[0]
    assert message.startswith("fix: redact URLs once and bound the report\n\nDetails.")
    assert "Co-Authored-By" not in message and "Review response, pass 1: R1-F1, R1-F2" in message
    assert h.git.pushes == [(run.worktree_path, settings.repositories["webapp"].remote, run.head_sha, "claude/change", old_head)]
    assert run.head_sha != old_head and run.extra["fix_commit_pass_1"] == run.head_sha
    findings = h.findings()
    assert findings["R1-F1"].state == "fixed_pending_verification" and findings["R1-F2"].state == "fixed_pending_verification"
    results = verification_repo.list_results(h.conn, run.id)
    assert results[0]["status"] == "passed" and results[0]["commands"] == [[".claude/run-tests.sh", "changed"]]
    check_call = next(call for call in h.process.calls if call["argv"] == [".claude/run-tests.sh", "changed"])
    assert check_call["cwd"] == run.worktree_path and "DB_URL" not in check_call["env"]


def test_a_pytest_check_runs_with_the_worktree_leading_pythonpath_so_its_child_processes_test_the_worktree(settings, tmp_path):
    repo = settings.repositories["webapp"]
    pytest_check = ["/opt/venv/bin/python", "-m", "pytest", "-q"]
    verification = dataclasses.replace(repo.verification, required=[pytest_check])
    h = harness(dataclasses.replace(settings, repositories={"webapp": dataclasses.replace(repo, verification=verification)}), tmp_path)
    h.process.script(pytest_check[:3], stdout="5 passed")
    run = to_verifying(h)

    run = step(h.deps, run)

    assert run.state == RunState.PUBLISHING
    check_call = next(call for call in h.process.calls if call["argv"] == pytest_check)
    assert check_call["env"]["PYTHONPATH"].split(os.pathsep)[0].startswith(run.worktree_path)


def test_a_pytest_config_the_coordinator_cannot_read_still_runs_the_check_for_pytest_to_judge(settings, tmp_path):
    repo = settings.repositories["webapp"]
    pytest_check = ["/opt/venv/bin/python", "-m", "pytest", "-q"]
    verification = dataclasses.replace(repo.verification, required=[pytest_check])
    h = harness(dataclasses.replace(settings, repositories={"webapp": dataclasses.replace(repo, verification=verification)}), tmp_path)
    h.process.script(pytest_check[:3], stdout="5 passed")
    run = to_verifying(h)
    worktree = Path(run.worktree_path)
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "pyproject.toml").write_text("[tool.pytest.ini_options]\npythonpath = 5\n")

    run = step(h.deps, run)

    assert run.state == RunState.PUBLISHING
    check_call = next(call for call in h.process.calls if call["argv"] == pytest_check)
    assert check_call["env"]["PYTHONPATH"].split(os.pathsep)[0] == run.worktree_path


def test_failing_checks_send_the_run_back_to_fix_once_then_pause(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.process.scripts = [s for s in h.process.scripts if s[0] != (".claude/run-tests.sh", "changed")]
    h.process.script([".claude/run-tests.sh", "changed"], exit_code=1, stdout="FAILED tests/XTest.php")
    run = to_verifying(h)

    run = step(h.deps, run)
    assert run.state == RunState.FIXING and h.git.commits == []
    h.author.reply(FIX)
    run = step(h.deps, run)
    assert "## Verification failure" in (tmp_path / "runs" / run.id / "pass-1" / "packet-fix.md").read_text()
    assert "FAILED tests/XTest.php" in (tmp_path / "runs" / run.id / "pass-1" / "packet-fix.md").read_text()

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.CHECKS_FAILED
    assert [r["status"] for r in verification_repo.list_results(h.conn, run.id)] == ["failed", "failed"]


def test_unavailable_checks_pause_at_once_and_never_count_as_passed(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.process.scripts = [s for s in h.process.scripts if s[0] != (".claude/run-tests.sh", "changed")]
    h.process.script([".claude/run-tests.sh", "changed"], exit_code=3, stdout="nothing maps to the changed files")
    run = to_verifying(h)

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.CHECKS_FAILED
    assert verification_repo.list_results(h.conn, run.id)[0]["status"] == "unavailable"
    assert h.git.commits == [] and h.git.pushes == []


def test_a_remote_head_that_moved_pauses_with_head_changed_and_pushes_nothing(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.github.add_pull(1004, head_sha="9" * 40, base_sha="b" * 40)

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.HEAD_CHANGED
    assert h.git.pushes == []


def test_a_lease_failure_at_push_time_pauses_with_head_changed(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.push_results.append(Err("head_changed"))

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.HEAD_CHANGED


# --- publish --------------------------------------------------------------------------------------

def test_publish_replies_in_each_thread_posts_one_summary_and_the_inbox_mirror_then_rereviews(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    review_write = h.github.writes[0]
    assert review_write[0] == "post_review" and review_write[4] == [LOGGER, NUMBERS]

    run = step(h.deps, run)

    assert run.state == RunState.REREVIEWING
    kinds = [write[0] for write in h.github.writes[1:]]
    assert kinds == ["reply", "reply", "post_comment", "post_comment"]
    replies = [write for write in h.github.writes if write[0] == "reply"]
    assert all("<!-- review-loop role=author" in write[2] and "finding=R1-F" in write[2] for write in replies)
    assert replies[0][2].startswith("Accepted") and run.head_sha[:9] in replies[0][2]
    summary = h.github.writes[3][2]
    assert summary.startswith(f"## Review response, pass 1 (head {run.head_sha[:9]})")
    assert "| R1-F1 |" in summary and "accepted" in summary and "passed" in summary
    mirror = h.github.writes[4][2]
    assert mirror.startswith("## Adjacent findings (not in this PR)") and ASSESS_984["adjacent_findings"][0]["title"] in mirror
    assert run.extra["inbox_comment_id"]


def test_publish_after_a_restart_posts_nothing_twice(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)
    run = step(h.deps, run)
    writes_before = len(h.github.writes)
    run.state = RunState.PUBLISHING
    runs_repo.save_run(h.conn, run)

    run = step(h.deps, run)

    assert run.state == RunState.REREVIEWING and len(h.github.writes) == writes_before


def test_the_inbox_mirror_is_edited_in_place_on_a_later_pass(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, to_publishing(h))
    first_mirror_id = run.extra["inbox_comment_id"]
    h.reviewer.reply({**REVIEW_984, "findings": REVIEW_984["findings"][:1], "resolved_prior": [
        {"id": "R1-F1", "resolution": "verified", "note": "ok"}, {"id": "R1-F2", "resolution": "verified", "note": "ok"}]})
    run = step(h.deps, run)
    h.author.reply({"dispositions": [{"finding_id": "R2-F1", "disposition": "reject", "reply": "No.", "evidence": "e", "intended_fix": ""}],
                    "adjacent_findings": [{"title": "Another thing", "description": "d", "file": "f", "symbol": "", "evidence": "e", "impact": "low", "next_step": "n"}],
                    "summary": "s"})
    run = step(h.deps, run)

    run = step(h.deps, run)

    edits = [write for write in h.github.writes if write[0] == "edit_comment"]
    assert edits and edits[-1][1] == first_mirror_id and "Another thing" in edits[-1][2]
    assert run.extra["inbox_comment_id"] == first_mirror_id


# --- rereview and completion ----------------------------------------------------------------------

def test_a_rereview_that_verifies_every_fix_completes_the_run_and_writes_the_report(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, to_publishing(h))
    h.reviewer.reply(verified("R1-F1", "R1-F2"))

    run = step(h.deps, run)

    assert run.state == RunState.COMPLETE and run.outcome.value == "complete" and run.pass_no == 2
    assert all(finding.state == "verified" for finding in h.findings().values())
    report = (tmp_path / "runs" / run.id / "report.md").read_text()
    assert "Review complete" in report and "R1-F1" in report
    assert h.deps.notifier.messages and "complete" in h.deps.notifier.messages[-1][1].lower()


def test_a_dispute_without_new_evidence_counts_as_accepting_the_rejection(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply(REJECTIONS)
    run = step(h.deps, run)
    run = step(h.deps, run)
    h.reviewer.reply({**APPROVE, "resolved_prior": [{"id": "R1-F1", "resolution": "disputed", "note": ""},
                                                     {"id": "R1-F2", "resolution": "rejection_accepted", "note": "fair"}]})
    h.git.working_changed = []

    run = step(h.deps, run)
    assert h.findings()["R1-F1"].state == "rejection_accepted" and run.state == RunState.VERIFYING
    run = step(h.deps, run)

    assert run.state == RunState.COMPLETE and h.git.commits == []


def test_silence_on_a_prior_finding_in_a_rereview_closes_it_by_omission(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, to_publishing(h))
    h.reviewer.reply(verified("R1-F1"))

    run = step(h.deps, run)

    assert h.findings()["R1-F2"].state == "verified"
    assert any(event["note"].get("note") == "by omission" for event in findings_repo.list_events(h.conn, run.id, "R1-F2"))


def test_new_findings_in_a_rereview_go_back_to_assessment_with_second_pass_ids(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, to_publishing(h))
    h.reviewer.reply({**REVIEW_984, "resolved_prior": [{"id": "R1-F1", "resolution": "verified", "note": ""}, {"id": "R1-F2", "resolution": "verified", "note": ""}]})

    run = step(h.deps, run)

    assert run.state == RunState.ASSESSING and "R2-F1" in h.findings()


def test_the_pass_budget_stops_the_run_with_exceptions_or_blocked(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.run.budgets = type(h.run.budgets)(2, 2, 1)
    runs_repo.save_run(h.conn, h.run)
    run = step(h.deps, to_publishing(h))
    blocker = {**REVIEW_984["findings"][0], "severity": "BLOCKER", "title": "Still leaks a token"}
    h.reviewer.reply({**REVIEW_984, "findings": [blocker], "resolved_prior": [{"id": "R1-F1", "resolution": "disputed", "note": "still leaks with two @"},
                                                                              {"id": "R1-F2", "resolution": "verified", "note": ""}]})

    run = step(h.deps, run)

    assert run.state == RunState.COMPLETE and run.outcome.value == "blocked"
    report = (tmp_path / "runs" / run.id / "report.md").read_text()
    assert "blocked" in report.lower() and "R2-F1" in report


# --- end to end -----------------------------------------------------------------------------------

def test_e2e_a_valid_finding_is_accepted_fixed_verified_pushed_and_verified_by_the_rereview(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.reviewer.reply(REVIEW_984)
    h.author.reply(ASSESS_984, session_id="sess-1")
    h.author.reply(FIX, session_id="sess-1")
    h.reviewer.reply(verified("R1-F1", "R1-F2"))

    run = run_loop(h.deps, h.run)

    assert run.state == RunState.COMPLETE and run.outcome.value == "complete"
    assert [write[0] for write in h.github.writes] == ["post_review", "reply", "reply", "post_comment", "post_comment", "post_review",
                                                       "resolve_thread", "resolve_thread", "post_comment"]
    assert len(h.git.commits) == 1 and len(h.git.pushes) == 1


def test_e2e_a_rejected_finding_accepted_by_the_reviewer_needs_no_commit(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.git.working_changed = []
    h.reviewer.reply(REVIEW_984)
    h.author.reply(REJECTIONS)
    h.reviewer.reply({**APPROVE, "resolved_prior": [{"id": "R1-F1", "resolution": "rejection_accepted", "note": "fair"},
                                                     {"id": "R1-F2", "resolution": "rejection_accepted", "note": "fair"}]})

    run = run_loop(h.deps, h.run)

    assert run.state == RunState.COMPLETE and h.git.commits == [] and h.git.pushes == []


def test_e2e_a_pr_closed_externally_stops_writes_and_keeps_history(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.reviewer.reply(REVIEW_984)
    h.author.reply(ASSESS_984)
    h.author.reply(FIX)
    run = step(h.deps, to_publishing(h))
    writes = len(h.github.writes)
    h.github.add_pull(1004, state="closed", merged=True, head_sha=run.head_sha, base_sha="b" * 40)

    run = run_loop(h.deps, run)

    assert run.state == RunState.CANCELLED and len(h.github.writes) == writes
    assert len(h.findings()) == 2


# --- the final report -----------------------------------------------------------------------------

def run_to_completion(h):
    h.reviewer.reply(REVIEW_984)
    h.author.reply(ASSESS_984)
    h.author.reply(FIX)
    h.reviewer.reply(verified("R1-F1", "R1-F2"))
    return run_loop(h.deps, h.run)


def test_e2e_the_final_report_tells_the_run_pass_by_pass_with_the_commit_read_back_from_git(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.git.changed = [LOGGER, NUMBERS]

    run = run_to_completion(h)

    sha = run.head_sha[:9]
    report = (tmp_path / "runs" / run.id / "report.md").read_text()
    assert f"- `{sha}` fix: redact URLs once and bound the report (pass 1): {LOGGER}, {NUMBERS}" in report
    assert f"Commit `{sha}` addressed R1-F1 and R1-F2. Checks passed." in report
    assert "The loop pushed 1 commit touching 2 files." in report
    final = [write for write in h.github.writes if write[0] == "post_comment"][-1][2]
    assert "## Pass by pass" in final and "## Commits" in final


def test_the_final_report_is_written_even_when_git_cannot_describe_a_commit(settings, tmp_path):
    h = harness(settings, tmp_path)

    def unreadable(*args):
        raise RuntimeError("git log failed")

    push = h.git.on_push
    h.git.on_push = lambda branch, sha: (push(branch, sha), setattr(h.git, "commit_info", unreadable))

    run = run_to_completion(h)

    assert run.state == RunState.COMPLETE
    report = (tmp_path / "runs" / run.id / "report.md").read_text()
    assert f"- `{run.head_sha[:9]}` (title unavailable) (pass 1)" in report
    assert "The loop pushed 1 commit. " in report


def test_a_final_report_longer_than_a_github_comment_is_posted_cut_and_kept_whole_on_disk(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("review_loop.services.completion_phase.COMMENT_LIMIT", 600)
    h = harness(settings, tmp_path)

    run = run_to_completion(h)

    final = [write for write in h.github.writes if write[0] == "post_comment"][-1][2]
    report_path = tmp_path / "runs" / run.id / "report.md"
    assert "The report was cut to fit a GitHub comment" in final and f"`runs/{run.id}/report.md`" in final
    assert str(tmp_path) not in final
    assert len(final.split("<!-- review-loop")[0]) <= 600
    assert "## Next step" in report_path.read_text()
