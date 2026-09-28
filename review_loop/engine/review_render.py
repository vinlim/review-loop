"""The reviewer's JSON as the review text the developer already knows, ready to post verbatim."""

from __future__ import annotations

VERDICT_TEXT = {"REQUEST_CHANGES": "REQUEST CHANGES", "APPROVE": "APPROVE", "COMMENT": "COMMENT"}


def render_review_body(review: dict, findings: list[tuple[str, dict]], marker: str, inline_ids: set[str] = frozenset()) -> str:
    lines = [
        "EXECUTIVE SUMMARY", "",
        f"Verdict: {VERDICT_TEXT.get(review['verdict'], review['verdict'])}",
        f"Risk Profile: {review['risk']}",
        f"Summary: {review['summary']}", "",
        f"Contract: owns {review['contract']['owns']}. Does not own {review['contract']['does_not_own']}.", "",
        "DETAILED FEEDBACK", "",
    ]
    detailed = [render_finding_comment(fid, finding, "") for fid, finding in findings if fid not in inline_ids]
    if detailed:
        lines.extend("\n".join(detailed).splitlines())
    elif findings:
        lines.append("See the inline comments; one per finding.")
    else:
        lines.append("No actionable findings.")
    for entry in review.get("resolved_prior", []):
        lines.append(f"\nPrior {entry['id']}: {entry['resolution']}. {entry['note']}".rstrip())
    for question in review.get("questions", []):
        lines.append(f"\n[QUESTION] {question['id']}{' (blocking)' if question.get('blocking') else ''}: {question['text']}")
    if review.get("verification"):
        lines += ["", "Verification:"] + [f"- {item}" for item in review["verification"]]
    lines += ["", "GitHub does not permit this account to formally request changes on its own PR; the verdict above is the review's verdict.", "", marker]
    return "\n".join(lines).rstrip() + "\n"


def render_finding_comment(finding_id: str, finding: dict, marker: str) -> str:
    lines = [
        f"{finding_id}",
        f"File: {finding['file']}" if finding.get("file") else "File: (none)",
        f"Severity: [{finding['severity']}]",
        f"Location: Line {finding['line']}" + (f" ({finding['symbol']})" if finding.get("symbol") else ""),
        f"Finding: {finding['title']}. {finding['finding']}",
        f"Protected behaviour: {finding['protected_behaviour']}",
        f"Evidence: {finding['evidence']}",
        f"Recommendation: {finding['recommendation']}",
    ]
    if finding.get("supersedes"):
        lines.append(f"Supersedes: {finding['supersedes']}")
    if finding.get("new_evidence"):
        lines.append(f"New evidence: {finding['new_evidence']}")
    if finding.get("proposed_refactor"):
        lines += ["Proposed Refactor:", "```", finding["proposed_refactor"].rstrip(), "```"]
    if marker:
        lines.append(marker)
    return "\n".join(lines) + "\n"
