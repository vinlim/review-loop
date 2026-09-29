

# --- reserving an effect is one statement that fails once a person's control has landed ---------------------------

def _run_row(conn, state, pause_reason=None):
    from review_loop.repositories import runs as runs_repo
    from review_loop.types.run import Budgets, PauseReason, Run, RunState

    run = Run(id="webapp-1004-x", repo="webapp", pr_number=1004, pr_url="u", pr_author="vinlim", head_ref="claude/x", base_ref="main",
              head_sha="h" * 40, base_sha="b" * 40, merge_base_sha="m" * 40, state=RunState(state), budgets=Budgets(7, 2, 1), versions={},
              pause_reason=PauseReason(pause_reason) if pause_reason else None, created_at="t", updated_at="t")
    runs_repo.create_run(conn, run)
    return run


def test_reserving_an_effect_records_intent_only_while_no_person_controls_the_run():
    from review_loop.repositories import outbox as outbox_repo
    from review_loop.repositories.db import connect, migrate

    for state, reason, expected in (("publishing", None, True), ("paused", "checks_failed", True), ("paused", "manual", False), ("cancelled", None, False)):
        conn = connect(":memory:")
        migrate(conn)
        run = _run_row(conn, state, reason)

        entry_id = outbox_repo.reserve_unless_controlled(conn, run.id, "review", {"pass": 1}, "marker-1", "t")

        assert (entry_id is not None) is expected, (state, reason)
        assert len(outbox_repo.list_entries(conn, run.id)) == (1 if expected else 0), (state, reason)
        if expected:
            assert outbox_repo.list_entries(conn, run.id)[0]["state"] == "intended"
