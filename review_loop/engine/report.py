"""The final report: what the developer reads at merge time, including every exception with both positions."""

from __future__ import annotations

from review_loop.engine.findings import FindingState, is_open_required
from review_loop.types.findings import Finding
from review_loop.types.run import Outcome, Run

HEADLINE = {Outcome.COMPLETE: "Review complete", Outcome.COMPLETE_WITH_EXCEPTIONS: "Review complete with exceptions",
            Outcome.BLOCKED: "Review blocked"}


def render_report(run: Run, findings: list[Finding], events: dict[str, list[dict]], decisions: list[dict],
                  verification: list[dict], inbox_items: list[dict], exhausted: bool) -> str:
    outcome = run.outcome or Outcome.COMPLETE
    lines = [f"# {HEADLINE[outcome]}: PR #{run.pr_number} at {run.head_sha[:9]} after {run.pass_no} pass{'es' if run.pass_no != 1 else ''}", ""]
    if exhausted:
        lines += [f"The review budget of {run.budgets.max_review_passes} passes ended with work still open; no further fix was attempted.", ""]
    lines += ["## Findings", "", "| Finding | Severity | State | Title |", "|---|---|---|---|"]
    lines += [f"| {f.id} | {f.severity} | {f.state} | {f.title} |" for f in findings] or ["| (none) | | | |"]
    attention = [f for f in findings if f.state == FindingState.DEFERRED_BY_DECISION or is_open_required(f)]
    if attention:
        lines += ["", "## Exceptions and open items", ""]
        for finding in attention:
            lines += _render_positions(finding, events.get(finding.id, []), decisions)
    re_raised = [(fid, e) for fid, items in events.items() for e in items if e["note"].get("re_raised")]
    if re_raised:
        lines += ["", "## Re-raised after a decision", ""] + [f"- {fid}: {e['note']['note']}" for fid, e in re_raised]
    if decisions:
        lines += ["", "## Alignment decisions", ""] + [f"- v{d['version']} ({d['source']}): {d['decision']}" for d in decisions]
    lines += ["", "## Verification", ""]
    lines += [f"- pass {v['pass_no']} attempt {v['attempt_no']}: {v['status']} ({', '.join(' '.join(c) for c in v['commands'])})" for v in verification] or ["- no checks ran"]
    lines += ["", f"## Adjacent findings filed: {len(inbox_items)}", ""] + [f"- #{item['id']} {item['title']}" for item in inbox_items]
    lines += ["", "## Next step", ""]
    if outcome == Outcome.BLOCKED:
        lines.append("Resolve the open blockers above, then mark the PR ready for hosted CI.")
    else:
        lines.append("Mark the PR ready to run hosted CI; merge as usual.")
    return "\n".join(lines) + "\n"


def _render_positions(finding: Finding, events: list[dict], decisions: list[dict]) -> list[str]:
    author = [e for e in events if e["actor"] == "author" and e["note"].get("reply")]
    reviewer = [e for e in events if e["actor"] == "reviewer" and e["note"].get("note")]
    arbiters = [e for e in events if e["actor"] == "arbiter"]
    lines = [f"### {finding.id} [{finding.severity}] {finding.state}: {finding.title}", f"Location: {finding.file}:{finding.line}",
             f"Protected behaviour: {finding.protected_behaviour}"]
    if reviewer:
        lines.append(f"Reviewer position: {reviewer[-1]['note']['note']}")
    if author:
        lines.append(f"Author position: {author[-1]['note']['reply']}")
    for index, event in enumerate(arbiters, start=1):
        lines.append(f"Arbitration (arbiter {index}): {event['note'].get('decision', '')}. {event['note'].get('rationale', '')}")
    related = [d for d in decisions if finding.id in d["finding_ids"]]
    for decision in related:
        lines.append(f"Decision v{decision['version']} ({decision['source']}): {decision['decision']}")
    lines.append("")
    return lines
