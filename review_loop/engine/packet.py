"""The context packet: everything a phase receives, rendered once so both agents read the same thing."""

from __future__ import annotations

from dataclasses import dataclass, field

from review_loop.types.findings import Finding
from review_loop.types.pull_request import PullRequest


@dataclass(frozen=True)
class PacketInput:
    pull: PullRequest
    merge_base_sha: str
    instruction_files: list[str]
    discussion_text: str
    findings: list[Finding]
    events: dict[str, list[str]]
    decisions: list[str]
    verification: list[str]
    phase: str
    active_ids: list[str]
    since_last_review: list[str]
    permitted_actions: str
    handoff: str = ""
    accepted_ids: list[str] = field(default_factory=list)


def render_packet(packet: PacketInput) -> str:
    pull = packet.pull
    sections = [
        f"# Context packet: PR #{pull.ref.number}",
        "## Pull request\n\n" + "\n".join([
            f"Title: {pull.title}", f"URL: {pull.ref.url}", f"Author: {pull.author}",
            f"Base: {pull.base_ref} at {pull.base_sha} (merge base {packet.merge_base_sha})",
            f"Head: {pull.head_ref} at {pull.head_sha}",
        ]) + "\n\n### Description\n\n" + (pull.body.strip() or "(no description)"),
    ]
    if packet.handoff:
        sections.append("## Author handoff\n\n" + packet.handoff)
    sections.append("## Instruction files\n\n" + (
        "Read these in the checkout: " + ", ".join(packet.instruction_files) if packet.instruction_files else "(none registered)"))
    sections.append("## Discussion so far\n\n" + (packet.discussion_text.strip() or "(none: this is the first review)"))
    if packet.since_last_review:
        sections.append("## Since your last review\n\n" + "\n".join(f"- {line}" for line in packet.since_last_review))
    by_id = {finding.id: finding for finding in packet.findings}
    if packet.active_ids:
        sections.append("## Active findings\n\n" + "\n".join(_render_finding(by_id[fid]) for fid in packet.active_ids if fid in by_id))
    if packet.accepted_ids:
        sections.append("## Accepted findings to fix\n\n" + "\n".join(_render_finding(by_id[fid]) for fid in packet.accepted_ids if fid in by_id))
    sections.append("## Ledger of prior findings\n\n" + (_render_ledger(packet) or "(none)"))
    sections.append("## Alignment decisions\n\n" + ("\n".join(f"- {decision}" for decision in packet.decisions) or "(none)"))
    sections.append("## Verification results\n\n" + ("\n".join(f"- {line}" for line in packet.verification) or "(none yet)"))
    sections.append("## Permitted actions in this phase\n\n" + packet.permitted_actions)
    return "\n\n".join(sections) + "\n"


def _render_finding(finding: Finding) -> str:
    lines = [
        f"### {finding.id} [{finding.severity}] {finding.title}",
        f"Location: {finding.file}:{finding.line}" + (f" ({finding.symbol})" if finding.symbol else ""),
        f"State: {finding.state}",
        f"Protected behaviour: {finding.protected_behaviour}",
        f"Finding: {finding.finding}",
        f"Evidence: {finding.evidence}",
        f"Recommendation: {finding.recommendation}",
    ]
    if finding.supersedes:
        lines.append(f"Supersedes: {finding.supersedes}")
    if finding.new_evidence:
        lines.append(f"New evidence: {finding.new_evidence}")
    if finding.proposed_refactor:
        lines += ["Proposed refactor:", "```", finding.proposed_refactor, "```"]
    return "\n".join(lines) + "\n"


def _render_ledger(packet: PacketInput) -> str:
    rows = []
    for finding in packet.findings:
        if finding.id in packet.active_ids:
            continue
        rows.append(f"- {finding.id} [{finding.severity}] {finding.state}: {finding.title} ({finding.file}:{finding.line})")
        rows.extend(f"  - {event}" for event in packet.events.get(finding.id, []))
    return "\n".join(rows)
