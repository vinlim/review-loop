"""Plain-text views of runs and findings; no adapter is constructed to render them."""

from __future__ import annotations

from review_loop.types.run import AgentChoice, PauseReason, Run, RunState

PREPARE_TAIL_LINES = 20


def render_status(runs: list[Run], unattended: set[str] = frozenset()) -> str:
    """`unattended` names the runs in a working state that no coordinator process is driving."""
    if not runs:
        return "no runs"
    lines = [f"{'run':<34} {'pr':<6} {'state':<28} {'pass':<8} updated"]
    for run in runs:
        state = _state(run) + (" (no coordinator)" if run.id in unattended else "")
        lines.append(f"{run.id:<34} #{run.pr_number:<5} {state:<28} {'pass ' + str(run.pass_no):<8} {run.updated_at}")
    return "\n".join(lines)


def render_show(run: Run, findings: list, configured: dict[str, AgentChoice] | None = None, unattended: bool = False) -> str:
    """`configured` is what the repository's config says now; it is printed only when it differs from what the run recorded."""
    state = _state(run) + (" (no coordinator; resume continues from this phase)" if unattended else "")
    lines = [
        f"run {run.id}: {run.pr_url}",
        f"state: {state}" + (f", outcome {run.outcome.value}" if run.outcome else ""),
        f"branch: {run.head_ref} at {run.head_sha}",
        f"base: {run.base_ref} at {run.base_sha} (merge base {run.merge_base_sha})",
        f"budgets: passes {run.pass_no}/{run.budgets.max_review_passes}, fix attempts {run.budgets.max_fix_attempts} per assessment, "
        f"alignment exchanges {run.budgets.max_alignment_exchanges}",
        "versions: " + (", ".join(f"{name} {version}" for name, version in sorted(run.versions.items())) or "none recorded"),
        f"agents: {_agents(run.agents)}",
        *([f"agents in config now: {_agents(configured)}"] if configured and configured != run.agents else []),
        f"worktree: {run.worktree_path or 'not prepared'}",
        *_prepare_failure_lines(run),
        "",
    ]
    if not findings:
        lines.append("no findings yet")
    for finding in findings:
        lines.append(f"{finding.id} [{finding.severity}] {finding.state}: {finding.title} ({finding.file}:{finding.line})")
    return "\n".join(lines)


def _agents(choices: dict[str, AgentChoice]) -> str:
    if not choices:
        return "not recorded"
    return "; ".join(f"{role} {choice.agent} model {choice.model or 'CLI default'} effort {choice.effort or 'CLI default'}"
                     for role, choice in choices.items())


def _prepare_failure_lines(run: Run) -> list[str]:
    if run.pause_reason != PauseReason.PREPARE_FAILED or not run.extra.get("prepare_failure"):
        return []
    tail = run.extra["prepare_failure"].rstrip().splitlines()[-PREPARE_TAIL_LINES:]
    return [f"prepare log: {run.extra.get('prepare_log', '')}", *("  " + line for line in tail)]


def _state(run: Run) -> str:
    if run.state == RunState.PAUSED and run.pause_reason:
        return f"paused ({run.pause_reason.value})"
    return run.state.value
