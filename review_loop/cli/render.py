"""Plain-text views of runs and findings; no adapter is constructed to render them."""

from __future__ import annotations

from review_loop.types.run import Run, RunState


def render_status(runs: list[Run]) -> str:
    if not runs:
        return "no runs"
    lines = [f"{'run':<34} {'pr':<6} {'state':<24} {'pass':<8} updated"]
    for run in runs:
        lines.append(f"{run.id:<34} #{run.pr_number:<5} {_state(run):<24} {'pass ' + str(run.pass_no):<8} {run.updated_at}")
    return "\n".join(lines)


def render_show(run: Run, findings: list) -> str:
    lines = [
        f"run {run.id}: {run.pr_url}",
        f"state: {_state(run)}" + (f", outcome {run.outcome.value}" if run.outcome else ""),
        f"branch: {run.head_ref} at {run.head_sha}",
        f"base: {run.base_ref} at {run.base_sha} (merge base {run.merge_base_sha})",
        f"budgets: passes {run.pass_no}/{run.budgets.max_review_passes}, fix attempts {run.budgets.max_fix_attempts} per assessment, "
        f"alignment exchanges {run.budgets.max_alignment_exchanges}",
        "versions: " + (", ".join(f"{name} {version}" for name, version in sorted(run.versions.items())) or "none recorded"),
        f"worktree: {run.worktree_path or 'not prepared'}",
        "",
    ]
    if not findings:
        lines.append("no findings yet")
    for finding in findings:
        lines.append(f"{finding.id} [{finding.severity}] {finding.state}: {finding.title} ({finding.file}:{finding.line})")
    return "\n".join(lines)


def _state(run: Run) -> str:
    if run.state == RunState.PAUSED and run.pause_reason:
        return f"paused ({run.pause_reason.value})"
    return run.state.value
