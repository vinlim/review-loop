import dataclasses
import json
from pathlib import Path

from review_loop.repositories import findings as findings_repo
from review_loop.repositories import inbox as inbox_repo
from review_loop.repositories import runs as runs_repo
from review_loop.repositories.db import connect, migrate
from review_loop.services.run_coordinator import Deps, run_loop, step
from review_loop.services.start import start_run
from review_loop.types.agents import AgentError, AgentFailure
from review_loop.types.run import PauseReason, RunState
from tests.fakes.agent import FakeAgent
from tests.fakes.clock import FakeClock
from tests.fakes.git import FakeGit
from tests.fakes.github import FakeGitHub
from tests.fakes.process import FakeProcessRunner

ROOT = Path(__file__).resolve().parents[2]
URL = "https://github.com/acme/webapp/pull/1004"
REVIEW_984 = json.loads((ROOT / "fixtures" / "agent_outputs" / "codex_review_984.json").read_text())
ASSESS_984 = json.loads((ROOT / "fixtures" / "agent_outputs" / "claude_assess_984_result.json").read_text())["structured_output"]
APPROVE = {"verdict": "APPROVE", "risk": "LOW", "summary": "clean", "contract": {"owns": "x", "does_not_own": "y"},
           "findings": [], "resolved_prior": [], "questions": [], "verification": []}


class Harness:
    def __init__(self, settings, tmp_path, inspect_only=False):
        self.conn = connect(":memory:")
        migrate(self.conn)
        self.github = FakeGitHub()
        self.github.add_pull(1004, head_sha="a" * 40, base_sha="b" * 40)
        self.git = FakeGit()
        self.git.merge_bases[("b" * 40, "a" * 40)] = "c" * 40
        self.process = FakeProcessRunner()
        self.process.script(["bash", ".claude/worktree-setup.sh"])
        self.reviewer, self.author = FakeAgent(), FakeAgent()
        self.clock = FakeClock()
        self.deps = Deps(settings=settings, conn=self.conn, git=self.git, github=self.github, process=self.process,
                         clock=self.clock, agents={"codex": self.reviewer, "claude": self.author}, prompts_dir=ROOT / "review_loop" / "prompts",
                         schemas_dir=ROOT / "review_loop" / "schemas", base_env={"PATH": "/usr/bin"}, runs_dir=tmp_path / "runs",
                         find_author_session=lambda agent, branch, local_path="": "desktop-session" if (agent, branch) == ("claude", "claude/change") else "",
                         inspect_only=inspect_only)
        self.run = start_run(URL, settings=settings, conn=self.conn, github=self.github, git=self.git, clock=self.clock, versions={},
                             inspect_only=inspect_only).value
        self.git.on_push = lambda branch, sha: self.github.move_head(1004, sha)

    def findings(self):
        return {finding.id: finding for finding in findings_repo.list_findings(self.conn, self.run.id)}


def test_prepare_sets_up_the_workspace_records_the_author_session_and_moves_to_reviewing(settings, tmp_path):
    h = Harness(settings, tmp_path)

    run = step(h.deps, h.run)

    assert run.state == RunState.REVIEWING
    assert run.worktree_path.endswith("webapp-1004") and run.local_branch == "review-loop/pr-1004"
    assert run.author_session == "desktop-session"
    assert ("worktree_add", str(settings.repositories["webapp"].local_path), run.worktree_path, "review-loop/pr-1004", "a" * 40) in h.git.calls
    assert runs_repo.get_run(h.conn, run.id).state == RunState.REVIEWING


def test_prepare_on_a_closed_pull_request_cancels_without_touching_git(settings, tmp_path):
    h = Harness(settings, tmp_path)
    h.github.add_pull(1004, state="closed", head_sha="a" * 40, base_sha="b" * 40)

    run = step(h.deps, h.run)

    assert run.state == RunState.CANCELLED
    assert not any(call[0] == "worktree_add" for call in h.git.calls)


def test_the_first_review_stores_findings_with_ids_and_moves_to_assessing(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984, session_id="thr-1")

    run = step(h.deps, run)

    assert run.state == RunState.ASSESSING and run.pass_no == 1
    findings = h.findings()
    assert set(findings) == {"R1-F1", "R1-F2"}
    assert findings["R1-F1"].state == "open" and findings["R1-F1"].severity == "ISSUE"
    assert findings["R1-F1"].file == "services/notifier/src/logger.ts" and findings["R1-F1"].line == 26
    assert len(findings["R1-F1"].fingerprint) == 12
    request = h.reviewer.requests[0]
    assert request.phase == "review" and request.tools_policy == "read-only" and request.cwd == run.worktree_path
    assert request.schema_path.endswith("schemas/review.json")
    assert "{{" not in request.prompt and "pull request #1004" in request.prompt
    pass_dir = tmp_path / "runs" / run.id / "pass-1"
    assert (pass_dir / "packet.md").exists() and (pass_dir / "diff.patch").read_text().startswith("diff --git")
    assert "## Active findings" not in (pass_dir / "packet.md").read_text()
    assert run.extra["last_reviewed_sha"] == "a" * 40


def test_an_approving_first_review_with_no_findings_goes_to_verification_before_completing(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(APPROVE)

    run = step(h.deps, run)

    assert run.state == RunState.VERIFYING and run.outcome is None


def test_a_usage_limit_during_review_pauses_without_consuming_a_pass(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.fail(AgentError(AgentFailure.USAGE_LIMIT, "limit"))

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.USAGE_LIMIT
    assert run.resume_state == RunState.REVIEWING and run.pass_no == 0


def test_a_malformed_review_is_retried_once_then_pauses(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.fail(AgentError(AgentFailure.SCHEMA_VIOLATION, "$.risk: bad"))
    h.reviewer.fail(AgentError(AgentFailure.MALFORMED_OUTPUT, "no file"))

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.AGENT_FAILED
    assert len(h.reviewer.requests) == 2


def test_the_assessment_applies_dispositions_files_adjacent_findings_and_moves_to_fixing(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply(ASSESS_984, session_id="sess-assess")

    run = step(h.deps, run)

    assert run.state == RunState.FIXING
    findings = h.findings()
    assert findings["R1-F1"].state == "accepted" and findings["R1-F2"].state == "accepted"
    events = findings_repo.list_events(h.conn, run.id, "R1-F1")
    assert any(event["to_state"] == "accepted" and event["note"]["reply"].startswith("Accepted") for event in events)
    items = inbox_repo.list_items(h.conn, repo="webapp")
    assert [item["title"] for item in items] == [a["title"] for a in ASSESS_984["adjacent_findings"]]
    assert items[0]["source_pr"] == 1004 and items[0]["status"] == "new"
    request = h.author.requests[0]
    assert request.phase == "assess" and request.tools_policy == "read-only" and request.resume_session_id == "desktop-session"
    assert "## Active findings" in (tmp_path / "runs" / run.id / "pass-1" / "packet-assess.md").read_text()
    assert run.author_session == "sess-assess"


def test_an_assessment_with_only_rejections_skips_the_fix_phase(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    rejections = {"dispositions": [{"finding_id": fid, "disposition": "reject", "reply": "No: the guard runs first.", "evidence": "X.php:3", "intended_fix": ""}
                                   for fid in ("R1-F1", "R1-F2")], "adjacent_findings": [], "summary": "both rejected"}
    h.author.reply(rejections)

    run = step(h.deps, run)

    assert run.state == RunState.PUBLISHING
    assert h.findings()["R1-F1"].state == "rejected_pending_review"


def test_an_assessment_missing_a_disposition_is_retried_once(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply({"dispositions": [], "adjacent_findings": [], "summary": "oops"})
    h.author.reply(ASSESS_984)

    run = step(h.deps, run)

    assert run.state == RunState.FIXING and len(h.author.requests) == 2


def test_inspect_only_runs_prepare_review_and_assessment_and_publishes_nothing(settings, tmp_path):
    h = Harness(settings, tmp_path, inspect_only=True)
    h.reviewer.reply(REVIEW_984)
    h.author.reply(ASSESS_984)

    run = run_loop(h.deps, h.run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.INSPECT_ONLY
    assert run.resume_state == RunState.FIXING
    assert h.github.writes == []
    assert (tmp_path / "runs" / run.id / "pass-1" / "review.md").exists()


def test_every_agent_request_grants_the_pass_directory_for_reading(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    run = step(h.deps, run)
    h.author.reply(ASSESS_984)
    step(h.deps, run)

    pass_dir = str(tmp_path / "runs" / run.id / "pass-1")
    assert h.reviewer.requests[0].read_dirs == [pass_dir]
    assert h.author.requests[0].read_dirs == [pass_dir]


def with_review(settings, **changes):
    repo = settings.repositories["webapp"]
    return dataclasses.replace(settings, repositories={"webapp": dataclasses.replace(repo, review=dataclasses.replace(repo.review, **changes))})


def test_each_phase_goes_to_the_agent_the_repository_configures_with_that_agents_model(settings, tmp_path):
    h = Harness(with_review(settings, reviewer="agy", reviewer_model="", reviewer_effort="", author="opencode", author_model="x/y"), tmp_path)
    agy, opencode = FakeAgent(), FakeAgent()
    h.deps.agents.update({"agy": agy, "opencode": opencode})
    run = step(h.deps, h.run)
    agy.reply(REVIEW_984)
    run = step(h.deps, run)
    opencode.reply(ASSESS_984)

    step(h.deps, run)

    assert h.reviewer.requests == [] and h.author.requests == []
    assert (agy.requests[0].phase, agy.requests[0].model, agy.requests[0].effort) == ("review", "", "")
    assert (opencode.requests[0].phase, opencode.requests[0].model) == ("assess", "x/y")
    assert {item["agent"] for item in inbox_repo.list_items(h.conn, repo="webapp")} == {"opencode"}


def test_a_read_only_phase_that_edits_the_worktree_pauses_once_and_leaves_the_edit_for_inspection(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    h.reviewer.on_run = lambda request: h.git.working_changed.append("app/Edited.php")

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.READ_ONLY_VIOLATED
    assert run.resume_state == RunState.REVIEWING and len(h.reviewer.requests) == 1
    assert h.git.working_changed == ["app/Edited.php"] and h.findings() == {}


def test_a_read_only_phase_that_commits_pauses(settings, tmp_path):
    h = Harness(settings, tmp_path)
    run = step(h.deps, h.run)
    h.reviewer.reply(REVIEW_984)
    h.reviewer.on_run = lambda request: h.git.heads.__setitem__(request.cwd, "9" * 40)

    run = step(h.deps, run)

    assert run.state == RunState.PAUSED and run.pause_reason == PauseReason.READ_ONLY_VIOLATED


def test_prepare_looks_for_a_desktop_session_of_the_configured_author_agent(settings, tmp_path):
    h = Harness(with_review(settings, author="opencode"), tmp_path)

    run = step(h.deps, h.run)

    assert run.state == RunState.REVIEWING and run.author_session == ""
