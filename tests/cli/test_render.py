from review_loop.cli.render import render_show, render_status
from review_loop.types.run import Budgets, PauseReason, Run, RunState


def run(state=RunState.REVIEWING, pause_reason=None):
    return Run(id="webapp-1004-20260927-100000", repo="webapp", pr_number=1004, pr_url="https://github.com/acme/webapp/pull/1004",
               pr_author="vinlim", head_ref="claude/x", base_ref="main", head_sha="a" * 40, base_sha="b" * 40, merge_base_sha="c" * 40,
               state=state, budgets=Budgets(7, 2, 1), versions={"codex": "0.157.1"}, pass_no=2, pause_reason=pause_reason,
               created_at="2026-09-27T10:00:00+00:00", updated_at="2026-09-27T11:30:00+00:00")


def test_status_lists_each_run_with_its_state_pass_and_pause_reason():
    text = render_status([run(), run(RunState.PAUSED, PauseReason.USAGE_LIMIT)])

    lines = [line for line in text.splitlines() if "webapp-1004" in line]
    assert len(lines) == 2
    assert "reviewing" in lines[0] and "pass 2" in lines[0]
    assert "paused (usage_limit)" in lines[1]


def test_show_prints_the_commits_budgets_and_versions_of_one_run():
    text = render_show(run(), findings=[])

    assert "a" * 40 in text and "c" * 40 in text
    assert "passes 2/7" in text and "codex 0.157.1" in text
    assert "no findings yet" in text
