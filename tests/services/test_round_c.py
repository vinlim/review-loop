"""Lifecycle, enrolment and containment findings from the adversarial reviews."""

from pathlib import Path

from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import runs as runs_repo
from review_loop.repositories import verification as verification_repo
from review_loop.services.run_coordinator import step
from review_loop.services.start import StartRefusal, start_run
from review_loop.types.agents import AgentError, AgentFailure
from review_loop.types.run import PauseReason, RunState
from tests.fakes.clock import FakeClock
from tests.services.test_alignment import NOTE, disagree, to_dispute
from tests.services.test_coordinator_m3 import FIX, REJECTIONS, harness, to_fixing, to_publishing, to_verifying, verified
from tests.services.test_coordinator_phases import APPROVE, ASSESS_984, REVIEW_984, URL, Harness

ROOT = Path(__file__).resolve().parents[2]


# --- F4: a clean review still verifies the tree before completing ------------------------------------

def test_an_approving_first_review_runs_the_checks_before_completing(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.git.working_changed = []
    run = step(h.deps, h.run)
    h.reviewer.reply(APPROVE)

    run = step(h.deps, run)
    assert run.state == RunState.VERIFYING
    run = step(h.deps, run)

    assert run.state == RunState.COMPLETE and run.outcome.value == "complete"
    assert verification_repo.list_results(h.conn, run.id)[0]["status"] == "passed" and h.git.commits == []


def test_a_clean_review_whose_checks_cannot_run_pauses_instead_of_completing(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.process.scripts = [s for s in h.process.scripts if s[0] != (".claude/run-tests.sh", "changed")]
    h.process.script([".claude/run-tests.sh", "changed"], exit_code=3, stdout="nothing maps")
    h.git.working_changed = []
    run = step(h.deps, h.run)
    h.reviewer.reply(APPROVE)
    run = step(h.deps, run)

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.CHECKS_FAILED and run.outcome is None


# --- F6, F7: enrolment ---------------------------------------------------------------------------------

def test_a_pull_request_from_a_fork_is_refused(settings):
    from review_loop.repositories.db import connect, migrate
    from tests.fakes.git import FakeGit
    from tests.fakes.github import FakeGitHub

    conn = connect(":memory:")
    migrate(conn)
    github = FakeGitHub()
    github.add_pull(1004, head_repo="someone/webapp")

    result = start_run(URL, settings=settings, conn=conn, github=github, git=FakeGit(), clock=FakeClock(), versions={})

    assert not result.ok and result.error == StartRefusal.FORK_NOT_SUPPORTED


def test_repository_matching_is_exact_on_owner_and_name(settings):
    from review_loop.services.start import find_repository
    from review_loop.types.pull_request import PullRef

    repo = settings.repositories["webapp"]
    settings.repositories["webapp-extra"] = type(repo)(**{**repo.__dict__, "name": "webapp-extra", "remote": "https://github.com/acme/webapp-extra.git"})

    assert find_repository(settings, PullRef("acme", "webapp", 1)).name == "webapp"
    assert find_repository(settings, PullRef("acme", "webapp-extra", 1)).name == "webapp-extra"
    assert find_repository(settings, PullRef("acme", "staff", 1)) is None


# --- F32, #11c: registered scripts are trusted only when the PR and the fix leave them alone -------------

def test_a_pr_that_changes_a_registered_script_pauses_before_anything_runs(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.git.changed = ["app/X.php", ".claude/run-tests.sh"]

    run = step(h.deps, h.run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.SCRIPTS_CHANGED
    assert not any(call[0] == "worktree_add" for call in h.git.calls)


def test_a_fix_that_touches_a_check_script_pauses_before_verification(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_fixing(h)
    h.git.working_changed.append(".claude/worktree-setup.sh")
    h.author.reply(FIX)
    run = step(h.deps, run)

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.SCRIPTS_CHANGED and h.git.commits == []


# --- F22: worktree reuse only on the tool's branch -------------------------------------------------------

def test_a_reused_worktree_on_another_branch_pauses_instead_of_resetting_it(settings, tmp_path):
    h = harness(settings, tmp_path)
    path = str(settings.repositories["webapp"].worktree_root / "webapp-1004")
    h.git.worktrees.add(path)
    h.git.branches[path] = "some/developer-branch"

    run = step(h.deps, h.run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.WORKSPACE_FOREIGN
    assert not any(call[0] == "reset_hard" for call in h.git.calls)


# --- F34, F35: assessment and rereview validation ---------------------------------------------------------

def test_an_answer_disposition_on_a_defect_is_malformed_and_retried(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    bad = {"dispositions": [{"finding_id": "R1-F1", "disposition": "answer", "reply": "42", "evidence": "", "intended_fix": ""},
                            {"finding_id": "R1-F2", "disposition": "reject", "reply": "no", "evidence": "e", "intended_fix": ""}],
           "adjacent_findings": [], "summary": "s"}
    h.author.reply(bad)
    h.author.reply(ASSESS_984)

    run = step(h.deps, run)

    assert run.state == RunState.FIXING and len(h.author.requests) == 2


def test_a_rereview_cannot_resolve_a_finding_it_raised_in_the_same_pass(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply({**REVIEW_984, "resolved_prior": [{"id": "R1-F1", "resolution": "verified", "note": "closing my own finding"}]})

    run = step(h.deps, run)

    assert h.findings()["R1-F1"].state == "open"


def test_verified_on_a_finding_that_was_never_fixed_is_ignored(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply(REJECTIONS)
    run = step(h.deps, run)
    run = step(h.deps, run)
    h.reviewer.reply({**APPROVE, "resolved_prior": [{"id": "R1-F1", "resolution": "verified", "note": ""},
                                                     {"id": "R1-F2", "resolution": "rejection_accepted", "note": ""}]})

    run = step(h.deps, run)

    assert h.findings()["R1-F1"].state == "rejected_pending_review"


# --- #9: silence never closes a rejected BLOCKER -----------------------------------------------------------

def test_a_rereview_that_omits_a_rejected_blocker_is_retried_then_pauses(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    blocker = {**REVIEW_984, "findings": [{**REVIEW_984["findings"][0], "severity": "BLOCKER"}, REVIEW_984["findings"][1]]}
    h.reviewer.reply(blocker)
    run = step(h.deps, run)
    h.author.reply(REJECTIONS)
    run = step(h.deps, run)
    run = step(h.deps, run)
    silent = {**APPROVE, "resolved_prior": [{"id": "R1-F2", "resolution": "rejection_accepted", "note": "fair"}]}
    h.reviewer.reply(silent)
    h.reviewer.reply(silent)

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.AGENT_FAILED
    assert h.findings()["R1-F1"].state == "rejected_pending_review" and len(h.reviewer.requests) == 3


# --- F17, alignment budget ---------------------------------------------------------------------------------

def test_a_dispute_on_the_last_pass_ends_the_run_instead_of_aligning(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.run.budgets = type(h.run.budgets)(2, 2, 1)
    runs_repo.save_run(h.conn, h.run)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply(REJECTIONS)
    run = step(h.deps, run)
    run = step(h.deps, run)
    h.reviewer.reply({**APPROVE, "verdict": "REQUEST_CHANGES", "resolved_prior": [
        {"id": "R1-F1", "resolution": "disputed", "note": "still leaks"}, {"id": "R1-F2", "resolution": "rejection_accepted", "note": ""}]})

    run = step(h.deps, run)

    assert run.state == RunState.COMPLETE and run.outcome.value == "complete_with_exceptions"


def test_a_second_dispute_on_an_already_arbitrated_finding_gets_the_severity_rule_without_agents(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply({"decision": "B", "rationale": "r", "residual": "none"})
    h.author.reply({"decision": "A", "rationale": "r", "residual": "none"})
    run = step(h.deps, run)
    assert h.findings()["R1-F1"].state == "rejection_accepted"
    run = step(h.deps, run)
    calls = len(h.reviewer.requests) + len(h.author.requests)
    h.reviewer.reply({**APPROVE, "verdict": "REQUEST_CHANGES", "findings": [{**REVIEW_984["findings"][0], "supersedes": "R1-F1", "new_evidence": "a third @ still leaks"}],
                      "resolved_prior": []})
    run = step(h.deps, run)
    assert h.findings()["R3-F1"].state == "open" and run.state == RunState.ASSESSING
    h.author.reply({"dispositions": [{"finding_id": "R3-F1", "disposition": "reject", "reply": "No.", "evidence": "e", "intended_fix": ""}], "adjacent_findings": [], "summary": "s"})
    run = step(h.deps, run)
    run = step(h.deps, run)
    h.reviewer.reply({**APPROVE, "verdict": "REQUEST_CHANGES", "resolved_prior": [{"id": "R3-F1", "resolution": "disputed", "note": "still"}]})
    run = step(h.deps, run)
    assert run.state == RunState.ALIGNING

    run = step(h.deps, run)

    assert h.findings()["R3-F1"].state == "deferred_by_decision"
    assert len(h.reviewer.requests) + len(h.author.requests) == calls + 3
    assert decisions_repo.list_decisions(h.conn, run.id)[-1]["source"] == "severity_rule"


# --- F37, #15: standing decisions and blockers ----------------------------------------------------------------

def test_a_blocker_that_supersedes_an_excepted_issue_is_not_excepted_by_the_standing_decision(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply({"decision": "A", "rationale": "r", "residual": "none"})
    h.author.reply({"decision": "A", "rationale": "r", "residual": "none"})
    run = step(h.deps, run)
    assert h.findings()["R1-F1"].state == "deferred_by_decision"
    run = step(h.deps, run)
    h.reviewer.reply({**APPROVE, "verdict": "REQUEST_CHANGES", "findings": [{**REVIEW_984["findings"][0], "severity": "BLOCKER", "supersedes": "R1-F1", "new_evidence": ""}],
                      "resolved_prior": []})

    run = step(h.deps, run)

    assert h.findings()["R3-F1"].state == "open" and run.state == RunState.ASSESSING


def test_a_fingerprint_match_alone_never_applies_a_standing_decision(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply({"decision": "B", "rationale": "r", "residual": "none"})
    h.author.reply({"decision": "A", "rationale": "r", "residual": "none"})
    run = step(h.deps, run)
    run = step(h.deps, run)
    h.reviewer.reply({**APPROVE, "verdict": "REQUEST_CHANGES", "findings": [{**REVIEW_984["findings"][0], "supersedes": "", "new_evidence": ""}], "resolved_prior": []})

    run = step(h.deps, run)

    finding = h.findings()["R3-F1"]
    assert finding.possible_duplicate_of == "R1-F1" and finding.state == "open"


# --- #7, #8, #11d: the fix and verify phases --------------------------------------------------------------------

def test_a_repair_attempt_keeps_the_fixes_the_first_attempt_declared(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.process.scripts = [s for s in h.process.scripts if s[0] != (".claude/run-tests.sh", "changed")]
    from review_loop.types.protocols import CompletedRun
    h.process.script_sequence([".claude/run-tests.sh", "changed"], [CompletedRun([], 1, "FAILED", ""), CompletedRun([], 0, "ok", "")])
    run = to_verifying(h)
    run = step(h.deps, run)
    assert run.state == RunState.FIXING
    h.author.reply({**FIX, "changes": FIX["changes"][1:], "not_changed": []})
    run = step(h.deps, run)

    run = step(h.deps, run)

    assert run.state == RunState.PUBLISHING
    assert h.findings()["R1-F1"].state == "fixed_pending_verification" and h.findings()["R1-F2"].state == "fixed_pending_verification"


def test_a_commit_the_agent_made_underneath_ours_is_never_pushed(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.heads[run.worktree_path] = "e" * 40
    h.git.commit_infos["e" * 40] = (run.head_sha, "agent commit\n\nCo-Authored-By: Claude <x>")

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.UNEXPECTED_COMMIT and h.git.pushes == []


def test_a_recovered_commit_carrying_a_trailer_is_not_pushed(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.working_changed = []
    h.git.heads[run.worktree_path] = "e" * 40
    h.git.commit_infos["e" * 40] = (run.head_sha, "fix: x\n\nReview response, pass 1: R1-F1\n\nCo-Authored-By: Claude <x>\n")

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.UNEXPECTED_COMMIT


def test_a_tree_that_changes_while_the_checks_run_is_not_committed(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.tree_hashes = ["1" * 40, "2" * 40]

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.UNEXPECTED_COMMIT and h.git.commits == []


def test_a_timed_out_check_is_unavailable_and_pauses_rather_than_sending_the_agent_to_repair(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.process.scripts = [s for s in h.process.scripts if s[0] != (".claude/run-tests.sh", "changed")]
    h.process.script([".claude/run-tests.sh", "changed"], exit_code=0, stdout="trapped TERM", timed_out=True)
    run = to_verifying(h)

    run = step(h.deps, run)

    assert verification_repo.list_results(h.conn, run.id)[0]["status"] == "unavailable"
    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.CHECKS_FAILED


# --- #13: a gateway error pauses the run instead of crashing it ---------------------------------------------------

def test_a_github_error_while_publishing_pauses_the_run_with_the_entry_left_uncertain(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_publishing(h)

    def explode(ref, comment_id, body):
        raise RuntimeError("gh api ... failed (exit 1): HTTP 404: Not Found")

    h.github.reply_to_comment = explode

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.GITHUB_ERROR
    from review_loop.repositories import outbox as outbox_repo
    assert any(entry["state"] == "uncertain" for entry in outbox_repo.list_entries(h.conn, run.id))


# --- #10, F10: nothing bound for GitHub carries provenance; arbiters are not named --------------------------------

def test_a_reply_carrying_a_provenance_line_is_cleaned_before_it_is_posted(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_fixing(h)
    events = findings_repo.list_events(h.conn, run.id, "R1-F1")
    assert events
    h.author.reply(FIX)
    run = step(h.deps, run)
    run = step(h.deps, run)
    findings_repo.add_event(h.conn, run.id, "R1-F1", "fixed_pending_verification", "fixed_pending_verification", "author",
                            {"disposition": "accept", "reply": "Fixed.\n\n🤖 Generated with Claude Code\nCo-Authored-By: Claude <x>", "evidence": "", "intended_fix": "", "pass": 1}, "t")

    run = step(h.deps, run)

    replies = [w[2] for w in h.github.writes if w[0] == "reply"]
    assert replies and all("Co-Authored-By" not in body and "Generated with" not in body for body in replies)


def test_the_posted_report_names_arbiters_by_number_not_by_model(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path, severity="BLOCKER")
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply({"decision": "A", "rationale": "Leak is real.", "residual": "none"})
    h.author.reply({"decision": "A", "rationale": "Never user input.", "residual": "none"})

    run = step(h.deps, run)

    final = [w[2] for w in h.github.writes if w[0] == "post_comment" and "Review blocked" in w[2]][-1]
    assert "Arbitration (arbiter 1)" in final and "Arbitration (arbiter 2)" in final
    assert "codex" not in final.lower() and "claude" not in final.lower()
