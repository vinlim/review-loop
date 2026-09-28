from review_loop.engine.findings import FindingState, Severity, fingerprint
from review_loop.engine.report import fit_for_comment, render_report
from review_loop.types.findings import Finding
from review_loop.types.run import Budgets, Outcome, Run, RunState


def test_the_report_lists_every_exception_with_both_positions_and_the_arbiters_rationales():
    run = Run(id="r", repo="webapp", pr_number=1004, pr_url="u", pr_author="v", head_ref="b", base_ref="main", head_sha="a" * 40, base_sha="b" * 40,
              merge_base_sha="c" * 40, state=RunState.COMPLETE, budgets=Budgets(7, 2, 1), versions={}, pass_no=3, outcome=Outcome.COMPLETE_WITH_EXCEPTIONS)
    finding = Finding(id="R1-F1", pass_no=1, severity=Severity.ISSUE, state=FindingState.DEFERRED_BY_DECISION, file="app/X.php", line=7, symbol="",
                      title="Notification failure handling", finding="", protected_behaviour="An order never exists without a notification attempt",
                      evidence="", recommendation="", proposed_refactor="", fingerprint=fingerprint("app/X.php", "", "t"))
    events = {"R1-F1": [
        {"actor": "reviewer", "to_state": "disputed", "note": {"note": "Roll back to keep all-or-nothing."}},
        {"actor": "author", "to_state": "rejected_pending_review", "note": {"reply": "Complete the order; retry notification separately."}},
        {"actor": "arbiter", "to_state": "deferred_by_decision", "note": {"arbiter": "codex", "decision": "keep", "rationale": "The contract excludes notification."}},
        {"actor": "arbiter", "to_state": "deferred_by_decision", "note": {"arbiter": "claude", "decision": "fix", "rationale": "The invariant reads all-or-nothing."}},
    ]}
    decisions = [{"version": 1, "source": "severity_rule", "finding_ids": ["R1-F1"], "decision": "exception: split verdicts on an ISSUE", "note": ""}]

    text = render_report(run, [finding], events, decisions, [], [], exhausted=False)

    assert text.startswith("# Review complete with exceptions: PR #1004 at aaaaaaaaa after 3 passes")
    section = text.split("## Exceptions and open items")[1]
    assert "Reviewer position: Roll back" in section and "Author position: Complete the order" in section
    assert "Arbitration (arbiter 1): keep. The contract excludes notification." in section
    assert "Arbitration (arbiter 2): fix. The invariant reads all-or-nothing." in section
    assert "Decision v1 (severity rule): exception" in section
    assert "### R1-F1 [ISSUE] Notification failure handling\nOutcome: exception by decision v1\n" in section
    assert "deferred_by_decision" not in text
    assert text.rstrip().endswith("Read the exceptions above, then mark the PR ready to run hosted CI.")


# --- the full account of a run ----------------------------------------------------------------------


C1, C2 = "1" * 40, "2" * 40
CHECKS = [[".claude/run-tests.sh", "changed"]]


def a_run(pass_no=3, outcome=Outcome.COMPLETE, **extra):
    return Run(id="r", repo="webapp", pr_number=1004, pr_url="u", pr_author="v", head_ref="b", base_ref="main", head_sha=C2, base_sha="b" * 40,
               merge_base_sha="c" * 40, state=RunState.COMPLETE, budgets=Budgets(7, 2, 1), versions={}, pass_no=pass_no, outcome=outcome,
               created_at="2026-09-27T10:00:00+00:00", extra=extra)


def a_finding(fid, state, severity=Severity.ISSUE, title="A title"):
    pass_no = int(fid[1])
    return Finding(id=fid, pass_no=pass_no, severity=severity, state=state, file="app/X.php", line=7, symbol="", title=title, finding="",
                   protected_behaviour="p", evidence="", recommendation="", proposed_refactor="", fingerprint=fingerprint("app/X.php", "", fid))


def event(actor, from_state, to_state, **note):
    return {"actor": actor, "from_state": from_state, "to_state": to_state, "note": note, "at": "2026-09-27T10:00:00+00:00"}


def fixed_then_verified(raised, fixed_in, commit, verified_in, **verify_note):
    return [event("reviewer", "", "open", **{"pass": raised}),
            event("author", "open", "accepted", disposition="accept", reply="Accepted.", **{"pass": raised}),
            event("coordinator", "accepted", "fixed_pending_verification", commit=commit, pushed=True, **{"pass": fixed_in}),
            event("reviewer", "fixed_pending_verification", "verified", **{"resolution": "verified", "note": "", "pass": verified_in, **verify_note})]


def three_pass_run():
    run = a_run(verdict_pass_1="REQUEST_CHANGES", verdict_pass_2="REQUEST_CHANGES", verdict_pass_3="APPROVE",
                head_pass_1="a" * 40, head_pass_2=C1, head_pass_3=C2)
    findings = [a_finding("R1-F1", "verified"), a_finding("R1-F2", "verified"),
                a_finding("R1-F3", "rejection_accepted", Severity.CHORE, "Rename the helper"), a_finding("R2-F1", "verified")]
    events = {
        "R1-F1": fixed_then_verified(1, 1, C1, 2),
        "R1-F2": fixed_then_verified(1, 1, C1, 2, resolution="by omission", note="by omission"),
        "R1-F3": [event("reviewer", "", "open", **{"pass": 1}),
                  event("author", "open", "rejected_pending_review", disposition="reject",
                        reply="The name follows the framework convention. Renaming breaks imports.", **{"pass": 1}),
                  event("reviewer", "rejected_pending_review", "rejection_accepted", resolution="rejection_accepted",
                        note="Fair, the convention wins.", **{"pass": 2})],
        "R2-F1": fixed_then_verified(2, 2, C2, 3),
    }
    verification = [{"pass_no": 1, "attempt_no": 1, "status": "passed", "commands": CHECKS},
                    {"pass_no": 2, "attempt_no": 1, "status": "passed", "commands": CHECKS}]
    commits = [{"sha": C1, "pass_no": 1, "title": "fix: redact URLs once", "files": ["services/notifier/src/logger.ts", "app/Support/JsonNumbers.php"], "pushed": True},
               {"sha": C2, "pass_no": 2, "title": "fix: cap reported paths", "files": ["app/Support/JsonNumbers.php"], "pushed": True}]
    return render_report(run, findings, events, [], verification, [], exhausted=False, commits=commits, finished_at="2026-09-27T12:05:00+00:00")


def test_the_overview_counts_passes_time_outcomes_commits_and_the_last_checks():
    overview = three_pass_run().split("## Findings")[0]

    assert "The loop ran 3 passes in 2h 5m." in overview
    assert "The reviewer raised 4 findings: 3 fixed and verified, 1 rejection accepted." in overview
    assert "The loop pushed 2 commits touching 2 files." in overview
    assert "The last checks, in pass 2, passed." in overview


def test_the_findings_table_says_how_each_finding_ended():
    table = three_pass_run().split("## Findings")[1].split("\n## ")[0]

    assert "| Finding | Severity | Raised | Outcome | Title |" in table
    assert "| R1-F1 | ISSUE | pass 1 | fixed in 111111111, verified in pass 2 | A title |" in table
    assert "| R1-F3 | CHORE | pass 1 | rejection accepted in pass 2 | Rename the helper |" in table


def test_a_finding_closed_without_a_fix_carries_the_authors_reason_and_the_reviewers_answer():
    section = three_pass_run().split("## Closed without a verified fix")[1].split("\n## ")[0]

    assert "**R1-F3** [CHORE] Rename the helper." in section
    assert 'The author declined: "The name follows the framework convention."' in section
    assert 'The reviewer accepted this in pass 2: "Fair, the convention wins."' in section
    assert "R1-F1" not in section


def test_each_pass_says_what_the_reviewer_and_author_did_and_what_was_committed_and_checked():
    passes = three_pass_run().split("## Pass by pass")[1].split("\n## ")[0].strip().splitlines()

    assert passes[0] == ("- **Pass 1** at `aaaaaaaaa`: request changes. Raised R1-F1, R1-F2 and R1-F3. "
                         "Author: accepted R1-F1 and R1-F2, rejected R1-F3. Commit `111111111` fixed R1-F1 and R1-F2. Checks passed.")
    assert passes[1] == ("- **Pass 2** at `111111111`: request changes. Reviewer: verified R1-F1 and R1-F2, accepted the rejection of R1-F3. "
                         "Raised R2-F1. Author: accepted R2-F1. Commit `222222222` fixed R2-F1. Checks passed.")
    assert passes[2] == "- **Pass 3** at `222222222`: approve. Reviewer: verified R2-F1. Raised nothing new."


def test_the_commits_section_lists_each_commit_with_its_pass_and_files():
    section = three_pass_run().split("## Commits")[1].split("\n## ")[0]

    assert "- `111111111` fix: redact URLs once (pass 1): services/notifier/src/logger.ts, app/Support/JsonNumbers.php" in section
    assert "- `222222222` fix: cap reported paths (pass 2): app/Support/JsonNumbers.php" in section


def test_a_long_file_list_is_cut_and_an_unpushed_commit_is_marked():
    files = [f"app/F{n}.php" for n in range(13)]
    commits = [{"sha": C1, "pass_no": 1, "title": "fix: many files", "files": files, "pushed": False}]

    text = render_report(a_run(pass_no=1), [], {}, [], [], [], exhausted=False, commits=commits)

    assert "- `111111111` fix: many files (pass 1, not pushed): app/F0.php" in text and "app/F9.php and 3 more" in text
    assert "The loop made 1 commit touching 13 files; 1 was not pushed." in text


def test_withdrawn_answered_and_kept_by_decision_findings_say_why():
    findings = [a_finding("R1-F1", "withdrawn"), a_finding("R1-F2", "answered", Severity.QUESTION), a_finding("R1-F3", "rejection_accepted")]
    events = {"R1-F1": [event("reviewer", "open", "withdrawn", resolution="withdrawn", note="The guard runs first.", **{"pass": 2})],
              "R1-F2": [event("author", "open", "answered", disposition="answer", reply="Yes, on purpose. The cache is per request.", **{"pass": 1})],
              "R1-F3": [event("coordinator", "needs_alignment", "rejection_accepted", note="keep: both arbiters kept it", **{"pass": 2})]}
    decisions = [{"version": 1, "source": "arbitration", "finding_ids": ["R1-F3"], "decision": "keep: both arbiters kept it", "note": ""}]

    text = render_report(a_run(pass_no=2), findings, events, decisions, [], [], exhausted=False)

    section = text.split("## Closed without a verified fix")[1].split("\n## ")[0]
    assert 'The reviewer withdrew it in pass 2: "The guard runs first."' in section
    assert 'Answered in pass 1: "Yes, on purpose."' in section
    assert "Kept as is by decision v1 (arbitration): keep: both arbiters kept it" in section
    assert "| R1-F3 | ISSUE | pass 1 | kept by decision v1 |" in text


def test_a_run_with_nothing_to_report_says_so_plainly():
    text = render_report(a_run(pass_no=1), [], {}, [], [], [], exhausted=False, finished_at="2026-09-27T10:45:00+00:00")

    overview = text.split("## Findings")[0]
    assert "The loop ran 1 pass in 45m." in overview
    assert "The reviewer raised no findings. The loop made no commits. No checks ran." in overview
    assert "## Closed without a verified fix" not in text and "## Commits" not in text and "## Adjacent findings" not in text
    assert "\n\n\n" not in text


def test_a_report_too_long_for_a_github_comment_is_cut_at_a_line_with_a_pointer_to_the_full_text():
    report = "# Review complete\n" + "".join(f"- line {n} of a long report\n" for n in range(5000))

    body = fit_for_comment(report, limit=2000, full_path="/state/runs/r/report.md")

    assert len(body) <= 2000 and body.startswith("# Review complete\n")
    assert body.rstrip().endswith("The full report is at `/state/runs/r/report.md` on the host that ran the loop.")
    assert fit_for_comment("short", limit=2000, full_path="x") == "short"


def test_a_pass_with_alignment_names_the_decisions_it_took():
    finding = a_finding("R1-F1", "deferred_by_decision")
    events = {"R1-F1": [event("reviewer", "", "open", **{"pass": 1}),
                        event("author", "open", "rejected_pending_review", disposition="reject", reply="No.", **{"pass": 1}),
                        event("reviewer", "rejected_pending_review", "disputed", resolution="disputed", note="Still leaks.", **{"pass": 2}),
                        event("coordinator", "disputed", "needs_alignment", note="alignment exchange", **{"pass": 2}),
                        event("coordinator", "needs_alignment", "deferred_by_decision", note="exception: split", **{"pass": 2})]}

    text = render_report(a_run(pass_no=2, outcome=Outcome.COMPLETE_WITH_EXCEPTIONS), [finding], events, [], [], [], exhausted=False)

    second = text.split("## Pass by pass")[1].strip().splitlines()[1]
    assert "Reviewer: disputed R1-F1." in second and "Decisions: alignment on R1-F1, exception for R1-F1." in second


def test_findings_that_all_ended_the_same_way_are_counted_once():
    one = render_report(a_run(pass_no=1), [a_finding("R1-F1", "deferred_by_decision")], {}, [], [], [], exhausted=False)
    two = render_report(a_run(pass_no=1), [a_finding("R1-F1", "verified"), a_finding("R1-F2", "verified")], {}, [], [], [], exhausted=False)

    assert "The reviewer raised 1 finding: left as an exception." in one
    assert "The reviewer raised 2 findings: all fixed and verified." in two


def test_a_finding_withdrawn_after_its_fix_names_the_fix_commit_so_the_report_never_contradicts_itself():
    finding = a_finding("R1-F1", "withdrawn")
    events = {"R1-F1": fixed_then_verified(1, 1, C1, 2)[:3] + [
        event("reviewer", "fixed_pending_verification", "withdrawn", resolution="withdrawn", note="The guard already ran first.", **{"pass": 2})]}

    text = render_report(a_run(pass_no=2), [finding], events, [], [], [], exhausted=False)

    assert "| R1-F1 | ISSUE | pass 1 | fixed in 111111111, withdrawn in pass 2 | A title |" in text
    section = text.split("## Closed without a verified fix")[1].split("\n## ")[0]
    assert 'Commit `111111111` fixed it in pass 1. The reviewer withdrew it in pass 2: "The guard already ran first."' in section
    assert "## Closed without a fix" not in text


def test_commits_git_could_not_describe_leave_the_file_count_out_or_qualify_it():
    unread = {"sha": C1, "pass_no": 1, "title": "", "files": None, "pushed": True}
    read = {"sha": C2, "pass_no": 2, "title": "fix: y", "files": ["a.php", "b.php"], "pushed": True}

    alone = render_report(a_run(pass_no=1), [], {}, [], [], [], exhausted=False, commits=[unread])
    mixed = render_report(a_run(pass_no=2), [], {}, [], [], [], exhausted=False, commits=[unread, read])

    assert "The loop pushed 1 commit. " in alone and "touching" not in alone
    assert "- `111111111` (title unavailable) (pass 1)\n" in alone
    assert "The loop pushed 2 commits touching at least 2 files." in mixed
