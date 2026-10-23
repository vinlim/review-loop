"""Replies, round summaries and the inbox mirror as the developer reads them today."""

from __future__ import annotations

from review_loop.types.findings import Finding

# decide_fix and decide_keep are a person's decision recorded with `review-loop align`, not the author's disposition.
DISPOSITION_LEAD = {"accept": "Accepted.", "reject": "Rejected.", "needs_alignment": "Needs alignment.", "answer": "Answer.",
                    "unfixed": "Accepted, then not changed.", "decide_fix": "Developer decision: fix.", "decide_keep": "Developer decision: keep."}
DISPOSITION_ROW = {"decide_fix": "developer decision: fix", "decide_keep": "developer decision: keep"}
FIXING = frozenset({"accept", "decide_fix"})


def render_reply(finding: Finding, note: dict, fix_commit: str, verification: str, marker: str) -> str:
    disposition = note.get("disposition", "")
    lead = DISPOSITION_LEAD.get(disposition, "")
    if disposition in FIXING and fix_commit:
        lead = f"{lead} Fixed in {fix_commit[:9]}."
    elif disposition == "decide_fix":
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
    lines += [f"| {row['id']} | {DISPOSITION_ROW.get(row['disposition'], row['disposition'])} | {row['commit']} |" for row in rows]
    lines += ["", f"Verification: {verification or 'no checks ran this pass'}"]
    if drift:
        lines.append(f"Files touched outside the accepted findings: {', '.join(drift)}")
    if inbox_count:
        lines.append(f"Adjacent findings filed this pass: {inbox_count}")
    if threadless:
        lines += ["", "Replies to findings without an inline thread:", ""] + threadless
    lines += ["", marker]
    return "\n".join(lines) + "\n"


def _decision_summary(rows: list[dict]) -> str:
    """A pass that only acted on a person's decisions has no assessment summary of its own."""
    fixed = [row["id"] for row in rows if row["disposition"] == "decide_fix" and row["commit"]]
    pending = [row["id"] for row in rows if row["disposition"] == "decide_fix" and not row["commit"]]
    kept = [row["id"] for row in rows if row["disposition"] == "decide_keep"]
    parts = []
    if fixed:
        parts.append(f"Fixed on a developer decision: {', '.join(fixed)}.")
    if pending:
        parts.append(f"Fix decided by a developer, not made in this pass: {', '.join(pending)}.")
    if kept:
        parts.append(f"Kept as is on a developer decision: {', '.join(kept)}.")
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
