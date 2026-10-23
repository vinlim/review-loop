from review_loop.cli.main import main
from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import runs as runs_repo
from review_loop.services import run_control
from review_loop.services.phase_support import pause
from review_loop.services.run_coordinator import step
from review_loop.types.run import PauseReason, RunState
from tests.cli.test_main import container
from tests.services.test_alignment import to_assessment_with_nothing_open, to_dispute
from tests.services.test_coordinator_m3 import FIX, NUMBERS, harness
from tests.services.test_coordinator_phases import ASSESS_984, REVIEW_984


def test_align_override_records_a_decision_and_settles_the_disputed_findings(settings, tmp_path, capsys):
    h, run = to_dispute(settings, tmp_path)
    box = container(settings)
    box.conn = h.conn
    decision_file = tmp_path / "decision.md"
    decision_file.write_text("keep\nThe query token is never user input; the scrub rule is fine.\n")

    assert main(["align", run.id, "--file", str(decision_file)], container=box) == 0

    assert findings_repo.get_finding(h.conn, run.id, "R1-F1").state == "rejection_accepted"
    decision = decisions_repo.list_decisions(h.conn, run.id)[-1]
    assert decision["source"] == "override" and decision["rationale"]["kind"] == "keep"
    assert "recorded a keep decision for R1-F1" in capsys.readouterr().out


def test_align_override_rejects_a_file_without_a_verdict_line(settings, tmp_path, capsys):
    h, run = to_dispute(settings, tmp_path)
    box = container(settings)
    box.conn = h.conn
    decision_file = tmp_path / "decision.md"
    decision_file.write_text("I am not sure.\n")

    assert main(["align", run.id, "--file", str(decision_file)], container=box) == 2
    assert "fix" in capsys.readouterr().err


def resumed_after_a_fix_decision(settings, tmp_path):
    """The run paused in a pass-2 assessment with no finding open; a person decides to fix R1-F2 and resumes it."""
    h, run = to_assessment_with_nothing_open(settings, tmp_path)
    run = pause(h.deps, run, PauseReason.AGENT_FAILED)
    box = container(settings)
    box.conn = h.conn
    decision_file = tmp_path / "decision.md"
    decision_file.write_text("fix\nBound the batch path as well.\n")
    assert main(["align", run.id, "--file", str(decision_file), "--finding", "R1-F2"], container=box) == 0
    return h, run_control.resume(h.conn, runs_repo.get_run(h.conn, run.id), h.clock).value


def test_a_fix_decision_on_a_run_paused_in_assessment_resumes_into_the_fix_without_assessing_again(settings, tmp_path):
    h, run = resumed_after_a_fix_decision(settings, tmp_path)
    assert run.state == RunState.ASSESSING and findings_repo.get_finding(h.conn, run.id, "R1-F2").state == "accepted"
    asked = len(h.author.requests)

    run = step(h.deps, run)

    assert run.state == RunState.FIXING and len(h.author.requests) == asked
    h.author.reply({**FIX, "changes": FIX["changes"][1:]})
    run = step(h.deps, run)
    assert h.author.requests[-1].phase == "fix" and run.state == RunState.VERIFYING
    packet = (tmp_path / "runs" / run.id / "pass-2" / "packet-fix.md").read_text()
    assert "## Accepted findings to fix\n\n### R1-F2" in packet and "Bound the batch path as well." in packet


def test_the_pass_fixed_on_a_decision_publishes_the_fix_in_the_summary_and_the_finding_thread(settings, tmp_path):
    h, run = resumed_after_a_fix_decision(settings, tmp_path)
    h.author.reply({**FIX, "changes": FIX["changes"][1:]})
    h.git.working_changed = [NUMBERS]
    for _ in ("assess", "fix", "verify"):
        run = step(h.deps, run)
    assert run.state == RunState.PUBLISHING
    posted = len(h.github.writes)

    run = step(h.deps, run)

    commit = run.extra["fix_commit_pass_2"][:9]
    writes = h.github.writes[posted:]
    summary = next(write[2] for write in writes if write[0] == "post_comment" and write[2].startswith("## Review response, pass 2"))
    assert f"| R1-F2 | developer decision: fix | {commit} |" in summary
    assert "Fixed on a developer decision: R1-F2." in summary
    replies = [write for write in writes if write[0] == "reply"]
    assert [write[1] for write in replies] == [findings_repo.get_finding(h.conn, run.id, "R1-F2").comment_id]
    assert replies[0][2].startswith(f"Developer decision: fix. Fixed in {commit}.") and "Bound the batch path as well." in replies[0][2]
    assert run.state == RunState.REREVIEWING


def test_a_fix_decision_in_the_pass_the_author_asked_for_alignment_replaces_that_reply(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply({"dispositions": [
        {"finding_id": "R1-F1", "disposition": "accept", "reply": "Confirmed.", "evidence": "logger.ts:26", "intended_fix": "redact once"},
        {"finding_id": "R1-F2", "disposition": "needs_alignment", "reply": "Turns on how far the bound reaches.", "evidence": "", "intended_fix": ""}],
        "adjacent_findings": [], "summary": "one accepted, one for alignment"})
    run = step(h.deps, run)
    assert run.state == RunState.FIXING
    box = container(settings)
    box.conn = h.conn
    decision_file = tmp_path / "decision.md"
    decision_file.write_text("fix\nBound the report by bytes.\n")
    assert main(["align", run.id, "--file", str(decision_file), "--finding", "R1-F2"], container=box) == 0
    h.author.reply(FIX)
    for _ in ("fix", "verify", "publish"):
        run = step(h.deps, run)

    commit = run.extra["fix_commit_pass_1"][:9]
    replies = {write[1]: write[2] for write in h.github.writes if write[0] == "reply"}
    reply = replies[findings_repo.get_finding(h.conn, run.id, "R1-F2").comment_id]
    assert reply.startswith(f"Developer decision: fix. Fixed in {commit}.") and "Turns on how far" not in reply
    summary = next(write[2] for write in h.github.writes if write[0] == "post_comment" and write[2].startswith("## Review response, pass 1"))
    assert f"| R1-F1 | accept | {commit} |" in summary and f"| R1-F2 | developer decision: fix | {commit} |" in summary


ONE_FOR_ALIGNMENT = {"dispositions": [
    {"finding_id": "R1-F1", "disposition": "accept", "reply": "Confirmed.", "evidence": "logger.ts:26", "intended_fix": "redact once"},
    {"finding_id": "R1-F2", "disposition": "needs_alignment", "reply": "Turns on how far the bound reaches.", "evidence": "", "intended_fix": ""}],
    "adjacent_findings": [], "summary": "one accepted, one for alignment"}


def decide_fix_for_r1_f2(settings, tmp_path, h, run_id):
    box = container(settings)
    box.conn = h.conn
    decision_file = tmp_path / "decision.md"
    decision_file.write_text("fix\nBound the report by bytes.\n")
    assert main(["align", run_id, "--file", str(decision_file), "--finding", "R1-F2"], container=box) == 0


def published_reply_and_summary(h, run, posted):
    writes = h.github.writes[posted:]
    replies = {write[1]: write[2] for write in writes if write[0] == "reply"}
    summary = next(write[2] for write in writes if write[0] == "post_comment" and write[2].startswith("## Review response"))
    return replies[findings_repo.get_finding(h.conn, run.id, "R1-F2").comment_id], summary


def test_a_fix_decision_recorded_after_verification_claims_no_commit_for_its_finding(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply(ONE_FOR_ALIGNMENT)
    run = step(h.deps, run)
    h.author.reply({**FIX, "changes": FIX["changes"][:1]})
    run = step(h.deps, step(h.deps, run))
    assert run.state == RunState.PUBLISHING
    run = pause(h.deps, run, PauseReason.GITHUB_ERROR, resume_state=RunState.PUBLISHING)
    decide_fix_for_r1_f2(settings, tmp_path, h, run.id)
    run = run_control.resume(h.conn, runs_repo.get_run(h.conn, run.id), h.clock).value
    posted = len(h.github.writes)

    run = step(h.deps, run)

    commit = run.extra["fix_commit_pass_1"][:9]
    reply, summary = published_reply_and_summary(h, run, posted)
    assert reply.startswith("Developer decision: fix. Not fixed in this pass.") and commit not in reply and "Verification:" not in reply
    assert "| R1-F2 | developer decision: fix |  |" in summary and f"| R1-F1 | accept | {commit} |" in summary


def test_a_fix_decision_recorded_while_the_author_assesses_outlasts_the_ignored_disposition(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)

    def decide_during_the_call(request):
        h.author.on_run = None
        decide_fix_for_r1_f2(settings, tmp_path, h, run.id)

    h.author.on_run = decide_during_the_call
    h.author.reply(ONE_FOR_ALIGNMENT)
    run = step(h.deps, run)
    assert run.state == RunState.FIXING and findings_repo.get_finding(h.conn, run.id, "R1-F2").state == "accepted"
    h.author.reply(FIX)
    run = step(h.deps, step(h.deps, run))
    posted = len(h.github.writes)

    run = step(h.deps, run)

    commit = run.extra["fix_commit_pass_1"][:9]
    reply, summary = published_reply_and_summary(h, run, posted)
    assert reply.startswith(f"Developer decision: fix. Fixed in {commit}.") and "Needs alignment." not in reply
    assert f"| R1-F2 | developer decision: fix | {commit} |" in summary


def test_a_decision_recorded_after_the_finding_replies_landed_reaches_the_thread_once(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply(ONE_FOR_ALIGNMENT)
    run = step(h.deps, run)
    h.author.reply({**FIX, "changes": FIX["changes"][:1]})
    run = step(h.deps, step(h.deps, run))

    def fail_the_summary_once(kind):
        if kind == "post_comment":
            h.github.on_call = None
            raise RuntimeError("502 from GitHub")

    h.github.on_call = fail_the_summary_once
    run = step(h.deps, run)
    assert (run.state, run.pause_reason) == (RunState.PAUSED, PauseReason.GITHUB_ERROR)
    decide_fix_for_r1_f2(settings, tmp_path, h, run.id)
    run = run_control.resume(h.conn, runs_repo.get_run(h.conn, run.id), h.clock).value

    run = step(h.deps, run)
    run.state = RunState.PUBLISHING
    runs_repo.save_run(h.conn, run)
    run = step(h.deps, run)

    thread = findings_repo.get_finding(h.conn, run.id, "R1-F2").comment_id
    replies = [write[2] for write in h.github.writes if write[0] == "reply" and write[1] == thread]
    assert len(replies) == 2 and replies[0].startswith("Needs alignment.")
    assert replies[1].startswith("Developer decision: fix.") and "Bound the report by bytes." in replies[1]
    assert run.state == RunState.REREVIEWING


def test_a_decision_on_a_finding_without_a_thread_recorded_after_the_summary_landed_is_posted_once(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    outside_the_diff = findings_repo.get_finding(h.conn, run.id, "R1-F2")
    outside_the_diff.comment_id = None
    findings_repo.save_finding(h.conn, run.id, outside_the_diff, h.clock.now().isoformat())
    h.author.reply({**ONE_FOR_ALIGNMENT, "adjacent_findings": ASSESS_984["adjacent_findings"]})
    run = step(h.deps, run)
    h.author.reply({**FIX, "changes": FIX["changes"][:1]})
    run = step(h.deps, step(h.deps, run))
    comments = {"n": 0}

    def fail_the_inbox_mirror_once(kind):
        if kind == "post_comment":
            comments["n"] += 1
            if comments["n"] == 2:
                h.github.on_call = None
                raise RuntimeError("502 from GitHub")

    h.github.on_call = fail_the_inbox_mirror_once
    run = step(h.deps, run)
    assert (run.state, run.pause_reason) == (RunState.PAUSED, PauseReason.GITHUB_ERROR)
    assert any(write[0] == "post_comment" and write[2].startswith("## Review response, pass 1") for write in h.github.writes)
    decide_fix_for_r1_f2(settings, tmp_path, h, run.id)
    run = run_control.resume(h.conn, runs_repo.get_run(h.conn, run.id), h.clock).value

    run = step(h.deps, run)
    run.state = RunState.PUBLISHING
    runs_repo.save_run(h.conn, run)
    run = step(h.deps, run)

    decisions = [write[2] for write in h.github.writes if write[0] == "post_comment" and "kind=decision finding=R1-F2" in write[2]]
    assert len(decisions) == 1
    assert decisions[0].startswith("**R1-F2**: Developer decision: fix.") and "Bound the report by bytes." in decisions[0]
    assert run.state == RunState.REREVIEWING
