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
from tests.services.test_coordinator_m3 import FIX


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


def test_a_fix_decision_on_a_run_paused_in_assessment_resumes_into_the_fix_without_assessing_again(settings, tmp_path):
    h, run = to_assessment_with_nothing_open(settings, tmp_path)
    run = pause(h.deps, run, PauseReason.AGENT_FAILED)
    box = container(settings)
    box.conn = h.conn
    decision_file = tmp_path / "decision.md"
    decision_file.write_text("fix\nBound the batch path as well.\n")
    assert main(["align", run.id, "--file", str(decision_file), "--finding", "R1-F2"], container=box) == 0
    run = run_control.resume(h.conn, runs_repo.get_run(h.conn, run.id), h.clock).value
    assert run.state == RunState.ASSESSING and findings_repo.get_finding(h.conn, run.id, "R1-F2").state == "accepted"
    asked = len(h.author.requests)

    run = step(h.deps, run)

    assert run.state == RunState.FIXING and len(h.author.requests) == asked
    h.author.reply({**FIX, "changes": FIX["changes"][1:]})
    run = step(h.deps, run)
    assert h.author.requests[-1].phase == "fix" and run.state == RunState.VERIFYING
    packet = (tmp_path / "runs" / run.id / "pass-2" / "packet-fix.md").read_text()
    assert "## Accepted findings to fix\n\n### R1-F2" in packet and "Bound the batch path as well." in packet
