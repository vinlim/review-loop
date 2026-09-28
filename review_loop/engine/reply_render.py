"""Replies, round summaries and the inbox mirror as the developer reads them today."""

from __future__ import annotations

from review_loop.types.findings import Finding

DISPOSITION_LEAD = {"accept": "Accepted.", "reject": "Rejected.", "needs_alignment": "Needs alignment.", "answer": "Answer.",
                    "unfixed": "Accepted, then not changed."}


def render_reply(finding: Finding, note: dict, fix_commit: str, verification: str, marker: str) -> str:
    lead = DISPOSITION_LEAD.get(note.get("disposition", ""), "")
    if note.get("disposition") == "accept" and fix_commit:
        lead = f"Accepted. Fixed in {fix_commit[:9]}."
    lines = [lead, "", note.get("reply", "").strip()]
    if note.get("evidence"):
        lines += ["", f"Evidence: {note['evidence'].strip()}"]
    if note.get("disposition") == "accept" and verification:
        lines += ["", f"Verification: {verification}"]
    lines += ["", marker]
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def render_summary(run_pr: int, pass_no: int, head_sha: str, summary: str, rows: list[dict], verification: str,
                   drift: list[str], inbox_count: int, threadless: list[str], marker: str) -> str:
    lines = [f"## Review response, pass {pass_no} (head {head_sha[:9]})", "", summary.strip(), "",
             "| Finding | Disposition | Commit |", "|---|---|---|"]
    lines += [f"| {row['id']} | {row['disposition']} | {row['commit']} |" for row in rows]
    lines += ["", f"Verification: {verification or 'no checks ran this pass'}"]
    if drift:
        lines.append(f"Files touched outside the accepted findings: {', '.join(drift)}")
    if inbox_count:
        lines.append(f"Adjacent findings filed this pass: {inbox_count}")
    if threadless:
        lines += ["", "Replies to findings without an inline thread:", ""] + threadless
    lines += ["", marker]
    return "\n".join(lines) + "\n"


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
