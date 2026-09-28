from review_loop.services.run_coordinator import run_loop
from review_loop.types.run import RunState
from tests.services.test_coordinator_m3 import ASSESS_984, FIX, REVIEW_984, harness, verified


def test_run_loop_reports_every_transition_in_order(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.reviewer.reply(REVIEW_984)
    h.author.reply(ASSESS_984, session_id="sess-1")
    h.author.reply(FIX, session_id="sess-1")
    h.reviewer.reply(verified("R1-F1", "R1-F2"))
    seen = []

    run_loop(h.deps, h.run, on_step=lambda run: seen.append((run.state, run.pass_no)))

    assert seen == [(RunState.REVIEWING, 0), (RunState.ASSESSING, 1), (RunState.FIXING, 1), (RunState.VERIFYING, 1),
                    (RunState.PUBLISHING, 1), (RunState.REREVIEWING, 1), (RunState.COMPLETE, 2)]
