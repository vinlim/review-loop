"""Replies, round summaries and the inbox mirror as the developer reads them today."""

from __future__ import annotations

from review_loop.types.findings import Finding

# A decision settles a finding in place of the author's disposition: a developer's, recorded with `review-loop align`, or the
# alignment phase's, from arbitration, the severity rule, or an earlier decision standing for a re-raised finding.
DECISION_SUMMARY = {"developer_fix": "Fixed on a developer decision", "developer_keep": "Kept as is on a developer decision",
                    "alignment_fix": "Fixed on an alignment decision", "alignment_keep": "Kept as is on an alignment decision",
                    "alignment_exception": "Left as an exception on an alignment decision"}
DISPOSITION_LEAD = {"accept": "Accepted.", "reject": "Rejected.", "needs_alignment": "Needs alignment.", "answer": "Answer.",
                    "unfixed": "Accepted, then not changed.", "developer_fix": "Developer decision: fix.",
                    "developer_keep": "Developer decision: keep.", "alignment_fix": "Alignment decision: fix.",
                    "alignment_keep": "Alignment decision: keep.", "alignment_exception": "Alignment decision: exception."}
# A fix decided after the pass verified its changes is made in a later pass.
NOT_MADE = {"developer_fix": "Fix decided by a developer, not made in this pass",
            "alignment_fix": "Fix decided in alignment, not made in this pass"}
FIXING = frozenset({"accept", "developer_fix", "alignment_fix"})


def render_reply(finding: Finding, note: dict, fix_commit: str, verification: str, marker: str) -> str:
    disposition = note.get("disposition", "")
    lead = DISPOSITION_LEAD.get(disposition, "")
    if disposition in FIXING and fix_commit:
        lead = f"{lead} Fixed in {fix_commit[:9]}."
    elif disposition in NOT_MADE:
        lead = f"{lead} Not fixed in this pass."
    lines = [lead, "", note.get("reply", "").strip()]
    if note.get("evidence"):
        lines += ["", f"Evidence: {note['evidence'].strip()}"]
    if disposition in FIXING and verification:
        lines += ["", f"Verification: {verification}"]
    lines += ["", marker]
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def render_summary(run_pr: int, pass_no: int, head_sha: str, summary: str, rows: list[dict], verification: str,
                   drift: list[str], inbox_count: int, threadless: list[str], marker: str) -> str:
    lines = [f"## Review response, pass {pass_no} (head {head_sha[:9]})", "", summary.strip() or _decision_summary(rows), "",
             "| Finding | Disposition | Commit |", "|---|---|---|"]
    lines += [f"| {row['id']} | {_row_label(row['disposition'])} | {row['commit']} |" for row in rows]
    lines += ["", f"Verification: {verification or 'no checks ran this pass'}"]
    if drift:
        lines.append(f"Files touched outside the accepted findings: {', '.join(drift)}")
    if inbox_count:
        lines.append(f"Adjacent findings filed this pass: {inbox_count}")
    if threadless:
        lines += ["", "Replies to findings without an inline thread:", ""] + threadless
    lines += ["", marker]
    return "\n".join(lines) + "\n"


def _row_label(disposition: str) -> str:
    """A decision reads as its lead ("alignment decision: fix"); the author's dispositions as themselves."""
    return DISPOSITION_LEAD[disposition].removesuffix(".").lower() if disposition in DECISION_SUMMARY else disposition


def _decision_summary(rows: list[dict]) -> str:
    """A pass with no assessment of its own still says what the decisions in it settled, and which decided fix it did not make."""
    parts = []
    for disposition, phrase in DECISION_SUMMARY.items():
        settled = [row for row in rows if row["disposition"] == disposition]
        not_made = [row["id"] for row in settled if disposition in NOT_MADE and not row["commit"]]
        made = [row["id"] for row in settled if row["id"] not in not_made]
        if made:
            parts.append(f"{phrase}: {', '.join(made)}.")
        if not_made:
            parts.append(f"{NOT_MADE[disposition]}: {', '.join(not_made)}.")
    return " ".join(parts)


def render_inbox_mirror(items: list[dict], marker: str) -> str:
    lines = ["## Adjacent findings (not in this PR)", "",
             "Noticed during review and parked; nothing here changes in this PR.", ""]
    if not items:
        lines.append("(none)")
    for item in items:
        location = f" ({item['file']})" if item.get("file") else ""
        lines.append(f"- #{item['id']} {item['title']}{location}: {item['next_step']}")
    lines += ["", marker]
    return "\n".join(lines) + "\n"
