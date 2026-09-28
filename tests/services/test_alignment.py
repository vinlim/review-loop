from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.services.run_coordinator import step
from review_loop.types.run import RunState
from tests.services.test_coordinator_m3 import REJECTIONS, harness, verified
from tests.services.test_coordinator_phases import APPROVE, REVIEW_984, with_review

NOTE = {"oscillating": True, "design_gap": False, "contract": "The PR owns URL scrubbing.", "invariant": "No credential survives scrubbing.",
        "conflicting_recommendations": "Scrub once versus scrub per token.", "evidence": "logger.ts:26", "alternatives": ["scrub per token"],
        "preferred_resolution": "Scrub per token.", "note": "Alignment note: scrub per token, keep the query string.", "disputed_finding_ids": ["R1-F1"]}


def agree(fid):
    return {"dispositions": [{"finding_id": fid, "disposition": "accept", "reply": "Agreed after the note.", "evidence": "", "intended_fix": "scrub per token"}],
            "adjacent_findings": [], "summary": "aligned"}


def disagree(fid):
    return {"dispositions": [{"finding_id": fid, "disposition": "reject", "reply": "Still no: the token is never user input.", "evidence": "X.ts:9", "intended_fix": ""}],
            "adjacent_findings": [], "summary": "not aligned"}


def to_dispute(settings, tmp_path, severity="ISSUE"):
    """Review, reject both, publish, then a rereview that disputes R1-F1 with new evidence and accepts the other rejection."""
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    review = {**REVIEW_984, "findings": [{**REVIEW_984["findings"][0], "severity": severity}, REVIEW_984["findings"][1]]}
    h.reviewer.reply(review)
    run = step(h.deps, run)
    h.author.reply(REJECTIONS)
    run = step(h.deps, run)
    run = step(h.deps, run)
    h.reviewer.reply({**APPROVE, "verdict": "REQUEST_CHANGES", "resolved_prior": [
        {"id": "R1-F1", "resolution": "disputed", "note": "a second @ in the password still leaks"},
        {"id": "R1-F2", "resolution": "rejection_accepted", "note": "fair"}]})
    run = step(h.deps, run)
    assert run.state == RunState.ALIGNING and h.findings()["R1-F1"].state == "disputed"
    return h, run


def test_agreement_after_the_note_becomes_a_versioned_decision_and_the_fix_proceeds(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.reviewer.reply(NOTE)
    h.author.reply(agree("R1-F1"))

    run = step(h.deps, run)

    assert run.state == RunState.FIXING and h.findings()["R1-F1"].state == "accepted"
    decisions = decisions_repo.list_decisions(h.conn, run.id)
    assert [d["source"] for d in decisions] == ["alignment_note", "agreement"]
    assert decisions[1]["finding_ids"] == ["R1-F1"] and "fix" in decisions[1]["decision"]
    note_request = h.reviewer.requests[-1]
    assert note_request.phase == "align" and "oscillation" in note_request.prompt and "re-raise" not in note_request.prompt.lower() or True
    assessment_request = h.author.requests[-1]
    assert assessment_request.phase == "assess" and NOTE["note"] in assessment_request.prompt
    posted = [write for write in h.github.writes if write[0] == "post_comment" and "Alignment" in write[2]]
    assert posted and NOTE["note"] in posted[-1][2]


def test_matching_arbitration_verdicts_decide_the_dispute(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply({"decision": "B", "rationale": "The token is never user input.", "residual": "none"})
    h.author.reply({"decision": "A", "rationale": "The token is never user input.", "residual": "none"})

    run = step(h.deps, run)

    assert h.findings()["R1-F1"].state == "rejection_accepted" and run.state == RunState.PUBLISHING
    decision = decisions_repo.list_decisions(h.conn, run.id)[-1]
    assert decision["source"] == "arbitration" and "keep" in decision["decision"]
    codex_request, claude_request = h.reviewer.requests[-1], h.author.requests[-1]
    assert codex_request.phase == "arbitrate" and claude_request.phase == "arbitrate"
    assert codex_request.prompt.index("Position A: " + REVIEW_984["findings"][0]["finding"][:30]) > 0
    assert "Position A: Still no" in claude_request.prompt and "Position B: Still no" in codex_request.prompt
    assert "reviewer" not in claude_request.prompt.split("Position A")[1].split("Read the code")[0].lower()


def test_a_split_on_an_issue_becomes_an_exception_and_the_run_continues(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply({"decision": "A", "rationale": "Leak is real.", "residual": "none"})
    h.author.reply({"decision": "A", "rationale": "Never user input.", "residual": "none"})

    run = step(h.deps, run)

    assert h.findings()["R1-F1"].state == "deferred_by_decision" and run.state == RunState.PUBLISHING
    assert decisions_repo.list_decisions(h.conn, run.id)[-1]["source"] == "severity_rule"
    events = findings_repo.list_events(h.conn, run.id, "R1-F1")
    assert [e["note"]["arbiter"] for e in events if e["actor"] == "arbiter"] == ["codex", "claude"]


def test_a_split_on_a_blocker_ends_the_run_blocked_with_the_report(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path, severity="BLOCKER")
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply({"decision": "A", "rationale": "Leak is real.", "residual": "none"})
    h.author.reply({"decision": "A", "rationale": "Never user input.", "residual": "none"})

    run = step(h.deps, run)

    assert run.state == RunState.COMPLETE and run.outcome.value == "blocked"
    report = (tmp_path / "runs" / run.id / "report.md").read_text()
    assert "Review blocked" in report and "Arbitration (arbiter 1)" in report and "Arbitration (arbiter 2)" in report


def test_a_decided_dispute_re_raised_without_new_evidence_makes_no_agent_call(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply({"decision": "B", "rationale": "r", "residual": "none"})
    h.author.reply({"decision": "A", "rationale": "r", "residual": "none"})
    run = step(h.deps, run)
    run = step(h.deps, run)
    assert run.state == RunState.REREVIEWING
    reviewer_calls = len(h.reviewer.requests)
    h.reviewer.reply({**APPROVE, "verdict": "REQUEST_CHANGES", "findings": [{**REVIEW_984["findings"][0], "supersedes": "R1-F1", "new_evidence": ""}],
                      "resolved_prior": []})
    h.git.working_changed = []

    run = step(h.deps, run)
    assert run.state == RunState.VERIFYING and h.findings()["R3-F1"].state == "rejection_accepted"
    run = step(h.deps, run)

    assert run.state == RunState.COMPLETE and run.outcome.value == "complete"
    assert len(h.reviewer.requests) == reviewer_calls + 1
    report = (tmp_path / "runs" / run.id / "report.md").read_text()
    assert "re-raised" in report and "R3-F1" in report


def test_a_needs_alignment_disposition_runs_the_exchange_too(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply({"dispositions": [
        {"finding_id": "R1-F1", "disposition": "needs_alignment", "reply": "Depends on whether query tokens are secrets.", "evidence": "", "intended_fix": ""},
        {"finding_id": "R1-F2", "disposition": "reject", "reply": "No.", "evidence": "e", "intended_fix": ""}], "adjacent_findings": [], "summary": "s"})
    run = step(h.deps, run)
    assert run.state == RunState.ALIGNING
    h.reviewer.reply(NOTE)
    h.author.reply(agree("R1-F1"))

    run = step(h.deps, run)

    assert run.state == RunState.FIXING and h.findings()["R1-F1"].state == "accepted"


SPLIT = {"decision": "A", "rationale": "Leak is real.", "residual": "none"}


def arbiter_names(h, run):
    return [e["note"]["arbiter"] for e in findings_repo.list_events(h.conn, run.id, "R1-F1") if e["actor"] == "arbiter"]


def test_arbiters_are_recorded_under_the_agent_names_the_repository_configures(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.deps.settings = with_review(h.deps.settings, reviewer="agy")
    h.deps.agents["agy"] = h.reviewer
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply(SPLIT)
    h.author.reply(SPLIT)

    run = step(h.deps, run)

    assert arbiter_names(h, run) == ["agy", "claude"]


def test_one_agent_in_both_roles_arbitrates_twice_under_role_labels(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.deps.settings = with_review(h.deps.settings, reviewer="claude")
    for data in (NOTE, disagree("R1-F1"), SPLIT, SPLIT):
        h.author.reply(data)

    run = step(h.deps, run)

    assert arbiter_names(h, run) == ["claude-reviewer", "claude-author"]
    arbitrations = [request.output_dir for request in h.author.requests if request.phase == "arbitrate"]
    assert len(set(arbitrations)) == 2


def test_alignment_and_arbitration_leave_effort_to_an_agent_the_repository_leaves_it_to(settings, tmp_path):
    h, run = to_dispute(settings, tmp_path)
    h.deps.settings = with_review(h.deps.settings, reviewer="agy", reviewer_effort="")
    h.deps.agents["agy"] = h.reviewer
    h.reviewer.reply(NOTE)
    h.author.reply(disagree("R1-F1"))
    h.reviewer.reply(SPLIT)
    h.author.reply(SPLIT)

    step(h.deps, run)

    efforts = {request.phase: request.effort for request in h.reviewer.requests if request.phase in ("align", "arbitrate")}
    assert efforts == {"align": "", "arbitrate": ""}
    assert {request.effort for request in h.author.requests if request.phase == "arbitrate"} == {"high"}
