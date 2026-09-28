from review_loop.config.settings import VerificationConfig
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import verification as verification_repo
from review_loop.services.run_coordinator import step
from review_loop.types.agents import AgentError, AgentFailure
from review_loop.types.result import Err
from review_loop.types.run import PauseReason, RunState
from tests.services.test_coordinator_m3 import FIX, harness, to_fixing, to_verifying


def with_verification(settings, **fields):
    repo = settings.repositories["webapp"]
    settings.repositories["webapp"] = type(repo)(**{**repo.__dict__, "verification": VerificationConfig(**{**repo.verification.__dict__, **fields})})
    return settings


def test_a_formatter_alone_never_counts_as_verification(settings, tmp_path):
    settings = with_verification(settings, required=[], format=[["vendor/bin/pint", "--dirty"]])
    h = harness(settings, tmp_path)
    h.process.script(["vendor/bin/pint", "--dirty"])
    run = to_verifying(h)

    run = step(h.deps, run)

    assert verification_repo.list_results(h.conn, run.id)[0]["status"] == "unavailable"
    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.CHECKS_FAILED and h.git.commits == []


def test_a_failing_formatter_fails_verification(settings, tmp_path):
    settings = with_verification(settings, format=[["vendor/bin/pint", "--dirty"]])
    h = harness(settings, tmp_path)
    h.process.script(["vendor/bin/pint", "--dirty"], exit_code=1, stderr="syntax error")
    run = to_verifying(h)

    run = step(h.deps, run)

    assert verification_repo.list_results(h.conn, run.id)[0]["status"] == "failed"


def test_a_fix_that_changed_nothing_makes_no_commit_and_turns_the_accepted_findings_into_pending_rejections(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_fixing(h)
    h.git.working_changed = []
    h.author.reply({**FIX, "changes": [], "not_changed": [{"finding_id": "R1-F1", "reason": "already bounded"}, {"finding_id": "R1-F2", "reason": "not reachable"}]})
    run = step(h.deps, run)

    run = step(h.deps, run)

    assert run.state == RunState.PUBLISHING and h.git.commits == [] and h.git.pushes == []
    assert all(f.state == "rejected_pending_review" for f in h.findings().values())


def test_an_accepted_finding_the_fix_output_neither_changed_nor_declined_is_not_marked_fixed(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_fixing(h)
    h.author.reply({**FIX, "changes": FIX["changes"][:1], "not_changed": []})
    run = step(h.deps, run)

    run = step(h.deps, run)

    findings = h.findings()
    assert findings["R1-F1"].state == "fixed_pending_verification"
    assert findings["R1-F2"].state == "rejected_pending_review"
    assert any("no change reported" in e["note"].get("reply", "") for e in findings_repo.list_events(h.conn, run.id, "R1-F2"))


def test_a_dirty_tree_at_the_start_of_a_fix_pauses_instead_of_committing_someone_elses_edits(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_fixing(h)
    h.git.clean[run.worktree_path] = False

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.WORKSPACE_DIRTY
    assert h.author.requests[-1].phase != "fix"


def test_a_write_phase_failure_is_never_retried_on_the_partial_workspace(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_fixing(h)
    h.author.fail(AgentError(AgentFailure.TIMEOUT, "took too long"))
    fix_requests_before = len([r for r in h.author.requests if r.phase == "fix"])

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.AGENT_FAILED
    assert len([r for r in h.author.requests if r.phase == "fix"]) == fix_requests_before + 1


def test_a_recovered_local_commit_is_reused_only_when_it_sits_on_the_expected_parent_with_the_pass_marker(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.working_changed = []
    h.git.heads[run.worktree_path] = "9" * 40
    h.git.commit_infos[("9" * 40)] = ("0" * 40, "someone else's commit")

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.UNEXPECTED_COMMIT
    assert h.git.pushes == []


def test_a_push_the_remote_does_not_confirm_pauses_the_run(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = to_verifying(h)
    h.git.push_results.append(Err("push_unverified"))

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.PUSH_FAILED


def test_each_agent_attempt_gets_its_own_output_directory(settings, tmp_path):
    h = harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.fail(AgentError(AgentFailure.SCHEMA_VIOLATION, "bad"))
    from tests.services.test_coordinator_phases import REVIEW_984
    h.reviewer.reply(REVIEW_984)

    step(h.deps, run)

    dirs = [r.output_dir for r in h.reviewer.requests]
    assert len(dirs) == 2 and dirs[0] != dirs[1] and dirs[0].endswith("attempt-1") and dirs[1].endswith("attempt-2")


def test_agents_receive_the_author_credential_but_prepare_and_check_commands_do_not(settings, tmp_path):
    h = harness(settings, tmp_path)
    h.deps.base_env["CLAUDE_CODE_OAUTH_TOKEN"] = "author-token"
    run = to_verifying(h)

    step(h.deps, run)

    agent_requests = h.reviewer.requests + h.author.requests
    assert agent_requests and all(r.env.get("CLAUDE_CODE_OAUTH_TOKEN") == "author-token" for r in agent_requests)
    project_calls = [c for c in h.process.calls if c["argv"][0] in ("bash", ".claude/run-tests.sh")]
    assert len(project_calls) >= 2 and all("CLAUDE_CODE_OAUTH_TOKEN" not in c["env"] for c in project_calls)
