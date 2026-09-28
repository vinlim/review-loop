from review_loop.cli.render import render_show, render_status
from review_loop.types.run import AgentChoice, Budgets, PauseReason, Run, RunState

AGENTS = {"reviewer": AgentChoice("codex", "gpt-5.6-sol", "xhigh"), "author": AgentChoice("claude", "claude-opus-5-5", "xhigh")}


def run(state=RunState.REVIEWING, pause_reason=None, extra=None, agents=AGENTS):
    return Run(id="webapp-1004-20260927-100000", repo="webapp", pr_number=1004, pr_url="https://github.com/acme/webapp/pull/1004",
               pr_author="vinlim", head_ref="claude/x", base_ref="main", head_sha="a" * 40, base_sha="b" * 40, merge_base_sha="c" * 40,
               state=state, budgets=Budgets(7, 2, 1), versions={"codex": "0.157.1"}, pass_no=2, pause_reason=pause_reason,
               created_at="2026-09-27T10:00:00+00:00", updated_at="2026-09-27T11:30:00+00:00", extra=extra or {}, agents=agents)


def test_status_lists_each_run_with_its_state_pass_and_pause_reason():
    text = render_status([run(), run(RunState.PAUSED, PauseReason.USAGE_LIMIT)])

    lines = [line for line in text.splitlines() if "webapp-1004" in line]
    assert len(lines) == 2
    assert "reviewing" in lines[0] and "pass 2" in lines[0]
    assert "paused (usage_limit)" in lines[1]


def test_show_prints_the_commits_budgets_versions_and_agents_of_one_run():
    text = render_show(run(), findings=[], configured=AGENTS)

    assert "a" * 40 in text and "c" * 40 in text
    assert "passes 2/7" in text and "codex 0.157.1" in text
    assert "agents: reviewer codex model gpt-5.6-sol effort xhigh; author claude model claude-opus-5-5 effort xhigh" in text
    assert "agents in config now" not in text
    assert "no findings yet" in text


def test_show_adds_the_current_config_when_it_differs_from_what_the_run_recorded():
    configured = {**AGENTS, "author": AgentChoice("opencode", "anthropic/claude-opus-5-5", "")}

    text = render_show(run(), findings=[], configured=configured)

    assert "agents: reviewer codex model gpt-5.6-sol effort xhigh; author claude model claude-opus-5-5 effort xhigh" in text
    assert "agents in config now: reviewer codex model gpt-5.6-sol effort xhigh; author opencode model anthropic/claude-opus-5-5 effort CLI default" in text


def test_show_says_when_a_run_predates_the_agent_record():
    text = render_show(run(agents={}), findings=[], configured=AGENTS)

    assert "agents: not recorded" in text
    assert "agents in config now: reviewer codex" in text


def test_show_prints_the_prepare_log_path_and_the_failure_tail_of_a_run_paused_by_a_failed_prepare():
    failure = "$ bash .claude/worktree-setup.sh\nexit 1\ncomposer install\ncomposer: not found"
    paused = run(RunState.PAUSED, PauseReason.PREPARE_FAILED, {"prepare_log": "/state/runs/r/prepare-1.log", "prepare_failure": failure})

    text = render_show(paused, findings=[])

    assert "paused (prepare_failed)" in text
    assert "prepare log: /state/runs/r/prepare-1.log" in text and "  composer: not found" in text


def test_show_keeps_prepare_details_out_of_a_run_that_moved_past_preparation():
    text = render_show(run(extra={"prepare_log": "/state/runs/r/prepare-2.log", "prepare_failure": "stale tail"}), findings=[])

    assert "prepare log" not in text and "stale tail" not in text


def test_status_marks_a_run_in_a_working_state_that_no_coordinator_is_driving():
    driven, dropped = run(), run(RunState.VERIFYING)
    dropped.id = "webapp-1005-20260927-100000"

    text = render_status([driven, dropped], unattended={dropped.id})

    lines = {line.split()[0]: line for line in text.splitlines() if "webapp-100" in line}
    assert "reviewing" in lines[driven.id] and "no coordinator" not in lines[driven.id]
    assert "verifying (no coordinator)" in lines[dropped.id]


def test_show_says_when_no_coordinator_is_driving_the_run_and_that_resume_continues_it():
    text = render_show(run(RunState.VERIFYING), findings=[], unattended=True)

    assert "state: verifying (no coordinator; resume continues from this phase)" in text


def test_status_says_when_a_paused_runs_coordinator_is_still_finishing_its_phase():
    paused = run(RunState.PAUSED, PauseReason.MANUAL)

    text = render_status([paused], finishing={paused.id})

    assert "paused (manual) (coordinator still finishing its phase)" in text
    assert "finishing" not in render_status([paused])


def test_show_tells_the_operator_to_wait_for_a_coordinator_still_finishing_its_phase():
    text = render_show(run(RunState.PAUSED, PauseReason.MANUAL), findings=[], finishing=True)

    assert "state: paused (manual) (coordinator still finishing its phase; resume after it exits)" in text
