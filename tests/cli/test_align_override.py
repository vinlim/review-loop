from review_loop.cli.main import main
from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.services.run_coordinator import step
from tests.cli.test_main import container
from tests.services.test_alignment import to_dispute


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
