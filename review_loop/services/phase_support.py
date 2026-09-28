"""Helpers every phase shares: agent runs with bounded retries, packets, persistence, pauses."""

from __future__ import annotations

import dataclasses
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from review_loop.config.settings import RepositoryConfig, Settings
from review_loop.engine.env import sanitize_env
from review_loop.engine.findings import FindingState, IllegalTransition, Severity, is_open_required, transition
from review_loop.engine.pull_url import parse_pull_url
from review_loop.repositories import decisions as decisions_repo
from review_loop.repositories import findings as findings_repo
from review_loop.repositories import phases as phases_repo
from review_loop.repositories import runs as runs_repo
from review_loop.repositories import verification as verification_repo
from review_loop.services import run_control
from review_loop.services.workspace import ensure_shims
from review_loop.types.agents import AgentError, AgentFailure, PhaseOutput, PhaseRequest
from review_loop.types.findings import Finding
from review_loop.types.pull_request import PullRef, PullRequest
from review_loop.types.result import Err, Ok, Result
from review_loop.types.run import Outcome, PauseReason, Run, RunState

RETRYABLE = {AgentFailure.TIMEOUT, AgentFailure.PROCESS_FAILED, AgentFailure.MALFORMED_OUTPUT, AgentFailure.SCHEMA_VIOLATION}
PAUSE_FOR = {AgentFailure.USAGE_LIMIT: PauseReason.USAGE_LIMIT, AgentFailure.AUTH_REQUIRED: PauseReason.AUTH_REQUIRED}
REVIEW_ACTIONS_TEXT = "Read anything in the checkout; run read-only commands; the sandbox refuses writes. Return the JSON the schema requires."
ASSESS_ACTIONS_TEXT = "Read, grep and run read-only git commands. No edits, no commits, no test runs; the fix phase and the coordinator do those."


@dataclass
class Deps:
    settings: Settings
    conn: sqlite3.Connection
    git: Any
    github: Any
    process: Any
    clock: Any
    reviewer: Any
    author: Any
    prompts_dir: Path
    schemas_dir: Path
    base_env: dict[str, str]
    runs_dir: Path
    find_author_session: Callable[[str], str]
    inspect_only: bool = False
    notifier: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


def run_agent(deps: Deps, run: Run, agent: Any, request: PhaseRequest, pass_no: int, state: RunState,
              validate: Callable[[dict], Result] | None = None) -> Result[PhaseOutput, PauseReason]:
    """Two attempts for retryable failures of a read-only phase; a write phase gets one, because a second run
    on a partially edited workspace is never safe. A usage or auth failure pauses at once."""
    attempts = 1 if request.tools_policy == "write" else 2
    for attempt in range(1, attempts + 1):
        attempt_request = dataclasses.replace(request, output_dir=str(Path(request.output_dir) / f"attempt-{attempt}"))
        attempt_id = phases_repo.record_attempt(deps.conn, run.id, request.phase, pass_no, attempt, now(deps), input_path=attempt_request.output_dir)
        result = agent.run(attempt_request)
        if result.ok and validate is not None:
            check = validate(result.value.data)
            if not check.ok:
                result = Err(AgentError(AgentFailure.MALFORMED_OUTPUT, check.error))
        if result.ok:
            phases_repo.finish_attempt(deps.conn, attempt_id, "ok", now(deps), result.value.session_id)
            return Ok(result.value)
        phases_repo.finish_attempt(deps.conn, attempt_id, "failed", now(deps), error={"kind": result.error.kind, "detail": result.error.detail})
        if result.error.kind in PAUSE_FOR:
            return Err(PAUSE_FOR[result.error.kind])
        if result.error.kind not in RETRYABLE:
            break
    return Err(PauseReason.AGENT_FAILED)


def request(deps: Deps, repo: RepositoryConfig, run: Run, phase: str, prompt: str, schema: str, output_dir: Path, policy: str,
             model: str, effort: str, resume: str = "") -> PhaseRequest:
    env = sanitize_env(deps.base_env, repo.workspace.sanitize_env, str(ensure_shims(deps.settings.state_dir)))
    return PhaseRequest(phase=phase, prompt=prompt, schema_path=str(deps.schemas_dir / f"{schema}.json"), cwd=run.worktree_path,
                        env=env, timeout_seconds=repo.review.timeouts_minutes.get(phase, 30) * 60, model=model, effort=effort,
                        output_dir=str(output_dir), tools_policy=policy, resume_session_id=resume, read_dirs=[str(Path(output_dir).parent)])


def adopt_head(deps: Deps, run: Run, repo: RepositoryConfig, pull: PullRequest) -> None:
    if pull.head_sha != run.head_sha or pull.base_sha != run.base_sha:
        run.head_sha, run.base_sha = pull.head_sha, pull.base_sha
        run.merge_base_sha = deps.git.merge_base(str(repo.local_path), pull.base_sha, pull.head_sha)


def instruction_files(repo: RepositoryConfig, run: Run) -> list[str]:
    return [name for name in repo.instruction_files if (Path(run.worktree_path) / name).exists()]


def events_by_finding(deps: Deps, run: Run) -> dict[str, list[str]]:
    lines: dict[str, list[str]] = {}
    for event in findings_repo.list_events(deps.conn, run.id):
        note = event["note"]
        text = f"{event['at'][:16]} {event['actor']}: {event['from_state'] or 'new'} -> {event['to_state']}"
        detail = note.get("reply") or note.get("note") or ""
        if detail:
            text += f": {detail[:300]}"
        lines.setdefault(event["finding_id"], []).append(text)
    return lines


def decisions(deps: Deps, run: Run) -> list[str]:
    return [f"v{d['version']} ({d['source']}, {', '.join(d['finding_ids'])}): {d['decision']}" for d in decisions_repo.list_decisions(deps.conn, run.id)]


def alignment_block(deps: Deps, run: Run) -> str:
    pending = [d for d in decisions_repo.list_decisions(deps.conn, run.id) if d["source"] == "alignment_note"]
    if not pending:
        return ""
    latest = pending[-1]
    return ("The reviewer noted an oscillation and passed on the following alignment note to mitigate it; assess the "
            "disputed findings against it:\n\n" + latest["note"])


def verification_lines(deps: Deps, run: Run) -> list[str]:
    return [f"pass {v['pass_no']} attempt {v['attempt_no']}: {v['status']} ({', '.join(' '.join(c) for c in v['commands'])})"
            for v in verification_repo.list_results(deps.conn, run.id)]


def open_blocking_question(findings: list[Finding]) -> bool:
    return any(f.severity == Severity.QUESTION and f.blocking and f.state == FindingState.OPEN for f in findings)


def outcome(findings: list[Finding]) -> Outcome:
    if any(is_open_required(f) and f.severity == Severity.BLOCKER for f in findings):
        return Outcome.BLOCKED
    if any(f.state == FindingState.DEFERRED_BY_DECISION for f in findings):
        return Outcome.COMPLETE_WITH_EXCEPTIONS
    return Outcome.COMPLETE


def pull_values(pull: PullRequest, run: Run) -> dict[str, str]:
    return {"pr_number": str(run.pr_number), "pr_url": run.pr_url, "pr_title": pull.title, "head_branch": run.head_ref,
            "head_sha": run.head_sha, "base_branch": run.base_ref, "base_sha": run.base_sha}


def template(deps: Deps, name: str) -> str:
    return (deps.prompts_dir / f"{name}.md").read_text()


def pass_dir(deps: Deps, run: Run, pass_no: int) -> Path:
    path = deps.runs_dir / run.id / f"pass-{pass_no}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def repo(deps: Deps, run: Run) -> RepositoryConfig:
    return deps.settings.repositories[run.repo]


def ref(run: Run) -> PullRef:
    return parse_pull_url(run.pr_url)


def now(deps: Deps) -> str:
    return deps.clock.now().isoformat()


def save(deps: Deps, run: Run, state: RunState) -> Run:
    """Persist a transition, unless another process stopped or paused the run meanwhile: that control wins."""
    persisted = runs_repo.get_run(deps.conn, run.id)
    if persisted is not None and externally_controlled(persisted) and state != RunState.CANCELLED:
        return persisted
    run.state = state
    run.updated_at = now(deps)
    runs_repo.save_run(deps.conn, run)
    return run


def externally_controlled(persisted: Run) -> bool:
    return persisted.state == RunState.CANCELLED or (persisted.state == RunState.PAUSED and persisted.pause_reason == PauseReason.MANUAL)


def inspect_only(deps: Deps, run: Run) -> bool:
    """Inspect mode belongs to the run: set at enrolment, never changed by a later invocation."""
    mode = run.extra.get("mode")
    if mode is None:
        paused_for_inspection = run.state == RunState.PAUSED and run.pause_reason == PauseReason.INSPECT_ONLY
        mode = "inspect" if (deps.inspect_only or paused_for_inspection) else "publish"
        run.extra["mode"] = mode
    return mode == "inspect"


def remote_head(run: Run) -> str:
    """The last commit this run knows to be on the PR branch; local unpushed commits do not move it."""
    return run.extra.get("remote_head", run.head_sha)


def check_pull_before_effect(deps: Deps, run: Run) -> Run | None:
    """Every external effect starts by re-reading the PR: closed cancels, a moved head pauses."""
    pull = deps.github.fetch_pull(ref(run))
    if pull.state != "open":
        return save(deps, run, RunState.CANCELLED)
    if pull.head_sha != remote_head(run):
        return pause(deps, run, PauseReason.HEAD_CHANGED, resume_state=run.state)
    return None


class transaction:
    """One SQLite transaction on the autocommit connection; rolls back when the block raises."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("begin")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("rollback" if exc_type else "commit")
        return False


def finish(deps: Deps, run: Run, state: RunState) -> Run:
    return save(deps, run, state)


def pause(deps: Deps, run: Run, reason: PauseReason, resume_state: RunState | None = None) -> Run:
    if resume_state is not None:
        run.resume_state = resume_state
        run.state = RunState.PAUSED
        run.pause_reason = reason
        return save(deps, run, RunState.PAUSED)
    return run_control.pause(deps.conn, run, reason, deps.clock)


def apply_events(deps: Deps, run: Run, finding: Finding, events: tuple[str, ...], actor: str, note: dict, at: str) -> bool:
    """Apply the first legal event of the candidates; record an ignored event when none is legal."""
    for event in events:
        try:
            new_state = transition(FindingState(finding.state), event)
        except IllegalTransition:
            continue
        findings_repo.add_event(deps.conn, run.id, finding.id, finding.state, new_state, actor, note, at)
        finding.state = new_state
        findings_repo.save_finding(deps.conn, run.id, finding, at)
        return True
    findings_repo.add_event(deps.conn, run.id, finding.id, finding.state, finding.state, actor, {**note, "ignored": "illegal transition"}, at)
    return False


def project_env(deps: Deps, repo_config: RepositoryConfig) -> dict[str, str]:
    return sanitize_env(deps.base_env, repo_config.workspace.sanitize_env, str(ensure_shims(deps.settings.state_dir)))


def publisher(deps: Deps, run: Run):
    from review_loop.services.publication import Publisher

    return Publisher(deps.conn, deps.github, deps.clock, forbidden=deps.settings.repositories[run.repo].publication.forbid_commit_trailers)


def trusted_logins(deps: Deps, run: Run) -> set[str]:
    return set(deps.settings.repositories[run.repo].trusted_logins) | {run.pr_author}
