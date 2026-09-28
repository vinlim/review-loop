"""Helpers every phase shares: agent runs with bounded retries, packets, persistence, pauses."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import sqlite3
import stat
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
from review_loop.types.agents import AGENT_PROFILES, AgentError, AgentFailure, PhaseOutput, PhaseRequest
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
    agents: dict[str, Any]
    prompts_dir: Path
    schemas_dir: Path
    base_env: dict[str, str]
    runs_dir: Path
    find_author_session: Callable[[str, str, str], str]
    inspect_only: bool = False
    notifier: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


def author_resume(run: Run, repo_config: RepositoryConfig) -> str:
    """The author session to resume. A session belongs to the CLI that made it, so after the author agent changes
    mid-run the new one starts fresh instead of failing on every resume."""
    owner = run.extra.get("author_session_agent") or (run.agents["author"].agent if "author" in run.agents else repo_config.review.author)
    return run.author_session if owner == repo_config.review.author else ""


def keep_author_session(run: Run, repo_config: RepositoryConfig, session_id: str) -> None:
    if session_id:
        run.author_session, run.extra["author_session_agent"] = session_id, repo_config.review.author


def run_agent(deps: Deps, run: Run, agent_name: str, request: PhaseRequest, pass_no: int, state: RunState,
              validate: Callable[[dict], Result] | None = None) -> Result[PhaseOutput, PauseReason]:
    """Two attempts for retryable failures of a read-only phase; a write phase gets one, because a second run
    on a partially edited workspace is never safe. A usage or auth failure pauses at once.

    An agent never starts on a checkout whose PR or fix changed a file its CLI runs at startup, since that code
    runs outside the agent's permissions. A read-only phase that moved HEAD, changed the working tree or wrote to
    a directory it may only read pauses whatever the agent returned: not every CLI can be held to read-only by its
    flags, so the coordinator checks the outcome itself."""
    untrusted = startup_changes(deps, run, agent_name, request.cwd)
    if untrusted:
        run.extra["scripts_changed"] = untrusted
        return Err(PauseReason.SCRIPTS_CHANGED)
    agent = deps.agents[agent_name]
    read_only = request.tools_policy != "write"
    attempts = 2 if read_only else 1
    for attempt in range(1, attempts + 1):
        attempt_request = dataclasses.replace(request, output_dir=str(Path(request.output_dir) / f"attempt-{attempt}"))
        before = read_only_state(deps, attempt_request) if read_only else None
        attempt_id = phases_repo.record_attempt(deps.conn, run.id, request.phase, pass_no, attempt, now(deps), input_path=attempt_request.output_dir)
        result = agent.run(attempt_request)
        breach = read_only_breach(deps, attempt_request, before) if read_only else ""
        if breach:
            phases_repo.finish_attempt(deps.conn, attempt_id, "failed", now(deps),
                                       error={"kind": PauseReason.READ_ONLY_VIOLATED, "detail": breach})
            return Err(PauseReason.READ_ONLY_VIOLATED)
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


@dataclass(frozen=True)
class ReadOnlyState:
    """What a read-only phase must leave as it found it, each file fingerprinted: the worktree's HEAD and every path
    git reports as changed or untracked, and every file in the directories the agent is granted for reading apart
    from the attempt's own output directory, where the adapter writes. Earlier attempts' output is compared like
    anything else. Ignored files are not."""

    head: str
    worktree: dict[str, str]
    granted: dict[str, str]


def read_only_state(deps: Deps, request: PhaseRequest) -> ReadOnlyState:
    worktree = {name: fingerprint(Path(request.cwd) / name) for name in deps.git.working_changed_files(request.cwd)}
    granted: dict[str, str] = {}
    for directory in request.read_dirs:
        granted.update(_granted_fingerprints(Path(directory), skip=Path(request.output_dir)))
    return ReadOnlyState(deps.git.head_sha(request.cwd), worktree, granted)


def read_only_breach(deps: Deps, request: PhaseRequest, before: ReadOnlyState) -> str:
    """Why the state no longer matches the one before a read-only phase, or "" when it does. State that can no longer
    be read counts: the agent may have removed or rewritten the worktree's .git, or left something still writing."""
    try:
        after = read_only_state(deps, request)
    except (RuntimeError, OSError) as error:
        return f"the worktree or a granted directory cannot be inspected after a read-only phase: {error}"
    if after.head != before.head:
        return "HEAD moved during a read-only phase"
    if changed := _differences(before.worktree, after.worktree):
        return f"the worktree changed during a read-only phase: {changed}"
    if changed := _differences(before.granted, after.granted):
        return f"a directory the agent may only read changed during a read-only phase: {changed}"
    return ""


def fingerprint(path: Path) -> str:
    """Content, mode and symlink target, so rewriting a file that was already changed still shows. A file its owner
    cannot read is fingerprinted as such, so a chmod shows as a changed mode instead of stopping the comparison."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "absent"
    if stat.S_ISLNK(info.st_mode):
        return f"link {os.readlink(path)}"
    if not stat.S_ISREG(info.st_mode):
        return f"mode {info.st_mode:o}"
    try:
        with path.open("rb") as handle:
            return f"{info.st_mode:o} {hashlib.file_digest(handle, 'sha256').hexdigest()}"
    except PermissionError:
        return f"{info.st_mode:o} unreadable"


def _granted_fingerprints(directory: Path, skip: Path) -> dict[str, str]:
    """Every file and symlink under directory, keyed by its path from the directory's parent, never following a link
    or entering skip. A directory that cannot be listed is recorded as such, so hiding a subtree still shows."""
    found: dict[str, str] = {}
    unlistable: list[OSError] = []
    for root, dirs, files in os.walk(directory, onerror=unlistable.append):
        dirs[:] = [name for name in dirs if Path(root, name) != skip]
        for path in [Path(root, name) for name in files] + [Path(root, name) for name in dirs if Path(root, name).is_symlink()]:
            found[str(path.relative_to(directory.parent))] = fingerprint(path)
    for error in unlistable:
        found[str(Path(error.filename).relative_to(directory.parent))] = f"unlistable: {error.strerror}"
    return found


def _differences(before: dict[str, str], after: dict[str, str]) -> str:
    changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    return ", ".join(changed[:10]) + (f" and {len(changed) - 10} more" if len(changed) > 10 else "")


def startup_changes(deps: Deps, run: Run, agent_name: str, path: str) -> list[str]:
    """Files the PR or a fix changed, or anything wrote untracked, that this agent's CLI would load and run at startup."""
    profile = AGENT_PROFILES[agent_name]
    if not profile.startup_files:
        return []
    changed = deps.git.paths_differing_from(path, run.merge_base_sha, profile.startup_roots())
    return sorted({changed_path for changed_path in changed if profile.runs_at_startup(changed_path)})


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
    """Persist a transition, unless a person stopped or paused the run meanwhile: that control wins, in one statement.
    A cancellation the phase decided itself (a closed PR) is written regardless."""
    run.state = state
    run.updated_at = now(deps)
    if state == RunState.CANCELLED:
        runs_repo.save_run(deps.conn, run)
        return run
    if runs_repo.save_run_unless_controlled(deps.conn, run):
        return run
    return runs_repo.get_run(deps.conn, run.id) or run


def externally_controlled(persisted: Run) -> bool:
    return persisted.state == RunState.CANCELLED or (persisted.state == RunState.PAUSED and persisted.pause_reason == PauseReason.MANUAL)


def inspect_only(deps: Deps, run: Run) -> bool:
    """Inspect mode belongs to the run: set at enrolment, never changed by a later invocation."""
    if not run.extra.get("mode"):
        run.extra["mode"] = "inspect" if deps.inspect_only else run.mode()
    return run.extra["mode"] == "inspect"


def remote_head(run: Run) -> str:
    """The last commit this run knows to be on the PR branch; local unpushed commits do not move it."""
    return run.extra.get("remote_head", run.head_sha)


def check_pull_before_effect(deps: Deps, run: Run) -> Run | None:
    """Every external effect starts by re-reading the PR: closed cancels, a moved head pauses."""
    pull = deps.github.fetch_pull(ref(run))
    if pull.state != "open":
        return save(deps, run, RunState.CANCELLED)
    if head_moved(deps, run):
        return pause(deps, run, PauseReason.HEAD_CHANGED, resume_state=run.state)
    return None


def head_moved(deps: Deps, run: Run) -> bool:
    """Whether the PR branch points somewhere other than the last head this run put or found there. Git is asked, never
    the API: GitHub can answer from before a push git already confirmed, whether the run's own or someone else's."""
    return deps.git.remote_branch_head(run.worktree_path, repo(deps, run).remote, run.head_ref) != remote_head(run)


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
