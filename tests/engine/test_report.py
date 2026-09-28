from review_loop.engine.findings import FindingState, Severity, fingerprint
from review_loop.engine.report import render_report
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
    assert "Decision v1 (severity_rule): exception" in section
