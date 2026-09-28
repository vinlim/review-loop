"""The final report: what the developer reads at merge time. An overview, how every finding ended, the run pass by
pass, the commits the loop made, and every exception with both positions."""

from __future__ import annotations

import re
from datetime import datetime

from review_loop.engine.findings import FindingState, is_open_required
from review_loop.types.findings import Finding
from review_loop.types.run import Outcome, Run

HEADLINE = {Outcome.COMPLETE: "Review complete", Outcome.COMPLETE_WITH_EXCEPTIONS: "Review complete with exceptions",
            Outcome.BLOCKED: "Review blocked"}
NEXT_STEP = {Outcome.COMPLETE: "Mark the PR ready to run hosted CI; merge as usual.",
             Outcome.COMPLETE_WITH_EXCEPTIONS: "Read the exceptions above, then mark the PR ready to run hosted CI.",
             Outcome.BLOCKED: "Resolve the open blockers above, then mark the PR ready for hosted CI."}
MAX_FILES_PER_COMMIT = 10
CLOSED_UNVERIFIED = (FindingState.REJECTION_ACCEPTED, FindingState.WITHDRAWN, FindingState.ANSWERED)
OVERVIEW_COUNTS = ((FindingState.VERIFIED, "fixed and verified", "fixed and verified"),
                   (FindingState.REJECTION_ACCEPTED, "rejection accepted", "rejections accepted"),
                   (FindingState.WITHDRAWN, "withdrawn", "withdrawn"),
                   (FindingState.ANSWERED, "answered", "answered"),
                   (FindingState.DEFERRED_BY_DECISION, "left as an exception", "left as exceptions"))
UNFINISHED = {FindingState.OPEN: "still open", FindingState.ACCEPTED: "accepted, not yet fixed",
              FindingState.FIXED_PENDING_VERIFICATION: "fixed, not yet verified",
              FindingState.REJECTED_PENDING_REVIEW: "rejected, not yet reviewed", FindingState.DISPUTED: "disputed",
              FindingState.NEEDS_ALIGNMENT: "awaiting alignment"}
CHECK_WORDS = {"passed": "passed", "failed": "failed", "unavailable": "could not run", "tree_changed": "saw the tree change under them"}
REVIEWER_ACTS = (("verified", "verified"), ("rejection_accepted", "accepted the rejection of"), ("withdrawn", "withdrew"),
                 ("disputed", "disputed"), ("open", "reopened"))
AUTHOR_ACTS = (("accept", "accepted"), ("reject", "rejected"), ("answer", "answered"), ("needs_alignment", "asked for alignment on"),
               ("unfixed", "left unchanged"))
DECISION_ACTS = (("needs_alignment", "alignment on"), ("accepted", "fix decided for"), ("rejection_accepted", "no fix for"),
                 ("deferred_by_decision", "exception for"))


def render_report(run: Run, findings: list[Finding], events: dict[str, list[dict]], decisions: list[dict],
                  verification: list[dict], inbox_items: list[dict], exhausted: bool, commits: list[dict] = (),
                  finished_at: str = "") -> str:
    outcome = run.outcome or Outcome.COMPLETE
    lines = [f"# {HEADLINE[outcome]}: PR #{run.pr_number} at {run.head_sha[:9]} after {_passes(run.pass_no)}", ""]
    if exhausted:
        lines += [f"The review budget of {run.budgets.max_review_passes} passes ended with work still open; no further fix was attempted.", ""]
    lines += [_overview(run, findings, verification, commits, finished_at)]
    lines += ["", "## Findings", "", "| Finding | Severity | Raised | Outcome | Title |", "|---|---|---|---|---|"]
    lines += [f"| {f.id} | {f.severity} | pass {f.pass_no} | {_outcome(f, events.get(f.id, []), decisions)} | {_cell(f.title)} |"
              for f in findings] or ["| (none) | | | | |"]
    attention = [f for f in findings if f.state == FindingState.DEFERRED_BY_DECISION or is_open_required(f)]
    if attention:
        lines += ["", "## Exceptions and open items", ""]
        for finding in attention:
            lines += _render_positions(finding, events.get(finding.id, []), decisions)
    closed = [f for f in findings if f.state in CLOSED_UNVERIFIED]
    if closed:
        lines += ["", "## Closed without a verified fix", ""] + [_closing(f, events.get(f.id, []), decisions) for f in closed]
    if run.pass_no:
        lines += ["", "## Pass by pass", ""] + [_pass_line(run, n, findings, events, verification) for n in range(1, run.pass_no + 1)]
    if commits:
        lines += ["", "## Commits", ""] + [_commit_line(commit) for commit in commits]
    re_raised = [(fid, e) for fid, items in events.items() for e in items if e["note"].get("re_raised")]
    if re_raised:
        lines += ["", "## Re-raised after a decision", ""] + [f"- {fid}: {e['note']['note']}" for fid, e in re_raised]
    if decisions:
        lines += ["", "## Alignment decisions", ""] + [f"- v{d['version']} ({_source(d)}): {d['decision']}" for d in decisions]
    lines += ["", "## Verification", ""]
    lines += [f"- pass {v['pass_no']} attempt {v['attempt_no']}: {v['status']} ({', '.join(' '.join(c) for c in v['commands'])})" for v in verification] or ["- no checks ran"]
    if inbox_items:
        lines += ["", f"## Adjacent findings filed: {len(inbox_items)}", ""] + [f"- #{item['id']} {item['title']}" for item in inbox_items]
    lines += ["", "## Next step", "", NEXT_STEP[outcome]]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)) + "\n"


def fit_for_comment(text: str, limit: int, full_path: str) -> str:
    """GitHub rejects a comment over 65,536 characters; a long report is cut at a line and points to the file on disk."""
    if len(text) <= limit:
        return text
    notice = f"\nThe report was cut to fit a GitHub comment. The full report is at `{full_path}` on the host that ran the loop.\n"
    cut = text[: limit - len(notice)]
    return cut[: cut.rfind("\n") + 1] + notice


def _overview(run: Run, findings: list[Finding], verification: list[dict], commits: list[dict], finished_at: str) -> str:
    elapsed = _elapsed(run.created_at, finished_at)
    sentences = [f"The loop ran {_passes(run.pass_no)}" + (f" in {elapsed}." if elapsed else ".")]
    if findings:
        known = {state for state, _, _ in OVERVIEW_COUNTS}
        counts = [(sum(1 for f in findings if f.state == state), one, many) for state, one, many in OVERVIEW_COUNTS]
        counts.append((sum(1 for f in findings if f.state not in known), "still open", "still open"))
        present = [(number, one, many) for number, one, many in counts if number]
        if len(present) == 1:
            number, one, many = present[0]
            summary = one if number == 1 else f"all {many}"
        else:
            summary = ", ".join(_count(number, one, many) for number, one, many in present)
        sentences.append(f"The reviewer raised {_count(len(findings), 'finding', 'findings')}: {summary}.")
    else:
        sentences.append("The reviewer raised no findings.")
    if commits:
        files = {path for commit in commits for path in commit["files"] or []}
        unread = any(commit["files"] is None for commit in commits)
        unpushed = sum(1 for commit in commits if not commit["pushed"])
        made = _count(len(commits), "commit", "commits")
        if files:
            made += f" touching {'at least ' if unread else ''}{_count(len(files), 'file', 'files')}"
        sentences.append(f"The loop pushed {made}." if not unpushed
                         else f"The loop made {made}; {unpushed} {'was' if unpushed == 1 else 'were'} not pushed.")
    else:
        sentences.append("The loop made no commits.")
    if verification:
        last = verification[-1]
        sentences.append(f"The last checks, in pass {last['pass_no']}, {_check_words(last['status'])}.")
    else:
        sentences.append("No checks ran.")
    return " ".join(sentences)


def _outcome(finding: Finding, events: list[dict], decisions: list[dict]) -> str:
    state = FindingState(finding.state)
    closing = _closing_event(events, state)
    in_pass = f" in pass {closing['note']['pass']}" if closing and closing["note"].get("pass") else ""
    decision = _decision_for(finding.id, decisions)
    commit, _ = _fix(events)
    fixed = f"fixed in {commit[:9]}, " if commit else ""
    if state == FindingState.VERIFIED:
        return f"{fixed}verified{in_pass}"
    if state == FindingState.REJECTION_ACCEPTED:
        if closing and closing["actor"] == "coordinator" and decision:
            return f"{fixed}kept by decision v{decision['version']}"
        return f"{fixed}rejection accepted{in_pass}"
    if state in (FindingState.WITHDRAWN, FindingState.ANSWERED):
        return f"{fixed}{state.value}{in_pass}"
    if state == FindingState.DEFERRED_BY_DECISION:
        return f"exception by decision v{decision['version']}" if decision else "left as an exception"
    return UNFINISHED.get(state, state.value.replace("_", " "))


def _closing(finding: Finding, events: list[dict], decisions: list[dict]) -> str:
    state = FindingState(finding.state)
    closing = _closing_event(events, state)
    note = closing["note"] if closing else {}
    in_pass = f" in pass {note['pass']}" if note.get("pass") else ""
    parts = [f"- **{finding.id}** [{finding.severity}] {_sentence(finding.title)}"]
    commit, fix_pass = _fix(events)
    if commit:
        parts.append(f"Commit `{commit[:9]}` fixed it in pass {fix_pass}.")
    if state == FindingState.REJECTION_ACCEPTED:
        reply = next((e["note"]["reply"] for e in reversed(events)
                      if e["actor"] == "author" and e["note"].get("disposition") in ("reject", "unfixed") and e["note"].get("reply")), "")
        if reply:
            parts.append(f"The author declined: {_quoted(reply)}")
        decision = _decision_for(finding.id, decisions)
        if closing and closing["actor"] == "coordinator" and decision:
            parts.append(f"Kept as is by decision v{decision['version']} ({_source(decision)}): {_sentence(decision['decision'])}")
        elif note.get("resolution") == "by omission":
            parts.append(f"The reviewer accepted this{in_pass} by not raising it again.")
        elif note.get("note"):
            parts.append(f"The reviewer accepted this{in_pass}: {_quoted(note['note'])}")
        else:
            parts.append(f"The reviewer accepted this{in_pass}.")
    elif state == FindingState.WITHDRAWN:
        parts.append(f"The reviewer withdrew it{in_pass}: {_quoted(note['note'])}" if note.get("note") else f"The reviewer withdrew it{in_pass}.")
    else:
        parts.append(f"Answered{in_pass}: {_quoted(note['reply'])}" if note.get("reply") else f"Answered{in_pass}.")
    return " ".join(parts)


def _pass_line(run: Run, n: int, findings: list[Finding], events: dict[str, list[dict]], verification: list[dict]) -> str:
    head, verdict = run.extra.get(f"head_pass_{n}", ""), run.extra.get(f"verdict_pass_{n}", "")
    parts = [f"- **Pass {n}**" + (f" at `{head[:9]}`" if head else "") + (f": {verdict.lower().replace('_', ' ')}." if verdict else ".")]
    in_pass = [(f.id, e) for f in findings for e in events.get(f.id, [])
               if e["note"].get("pass") == n and e["from_state"] and e["from_state"] != e["to_state"]]
    reviewer = _grouped([(fid, e["to_state"]) for fid, e in in_pass if e["actor"] == "reviewer"], REVIEWER_ACTS)
    if reviewer:
        parts.append(f"Reviewer: {reviewer}.")
    raised = [f.id for f in findings if f.pass_no == n]
    parts.append(f"Raised {_join(raised)}." if raised else "Raised no findings." if n == 1 else "Raised nothing new.")
    author = _grouped([(fid, e["note"].get("disposition", "")) for fid, e in in_pass if e["actor"] == "author"], AUTHOR_ACTS)
    if author:
        parts.append(f"Author: {author}.")
    decided = _grouped([(fid, e["to_state"]) for fid, e in in_pass if e["actor"] == "coordinator" and not e["note"].get("commit")], DECISION_ACTS)
    if decided:
        parts.append(f"Decisions: {decided}.")
    fixes = [(fid, e["note"]) for fid, e in in_pass if e["note"].get("commit")]
    if fixes:
        commit = fixes[0][1]
        parts.append(f"Commit `{commit['commit'][:9]}` fixed {_join(_unique(fid for fid, _ in fixes))}."
                     + ("" if commit.get("pushed", True) else " It was not pushed."))
    checks = [_check_words(v["status"]) for v in verification if v["pass_no"] == n]
    if checks:
        parts.append(f"Checks {', then '.join(checks)}.")
    return " ".join(parts)


def _commit_line(commit: dict) -> str:
    files = commit["files"] or []
    shown, more = files[:MAX_FILES_PER_COMMIT], len(files) - MAX_FILES_PER_COMMIT
    listing = ", ".join(shown) + (f" and {more} more" if more > 0 else "")
    where = f"pass {commit['pass_no']}" + ("" if commit["pushed"] else ", not pushed")
    return f"- `{commit['sha'][:9]}` {commit['title'] or '(title unavailable)'} ({where})" + (f": {listing}" if files else "")


def _render_positions(finding: Finding, events: list[dict], decisions: list[dict]) -> list[str]:
    author = [e for e in events if e["actor"] == "author" and e["note"].get("reply")]
    reviewer = [e for e in events if e["actor"] == "reviewer" and e["note"].get("note")]
    arbiters = [e for e in events if e["actor"] == "arbiter"]
    lines = [f"### {finding.id} [{finding.severity}] {finding.title}", f"Outcome: {_outcome(finding, events, decisions)}",
             f"Location: {finding.file}:{finding.line}", f"Protected behaviour: {finding.protected_behaviour}"]
    if reviewer:
        lines.append(f"Reviewer position: {reviewer[-1]['note']['note']}")
    if author:
        lines.append(f"Author position: {author[-1]['note']['reply']}")
    for index, event in enumerate(arbiters, start=1):
        lines.append(f"Arbitration (arbiter {index}): {event['note'].get('decision', '')}. {event['note'].get('rationale', '')}")
    related = [d for d in decisions if finding.id in d["finding_ids"]]
    for decision in related:
        lines.append(f"Decision v{decision['version']} ({_source(decision)}): {decision['decision']}")
    lines.append("")
    return lines


def _closing_event(events: list[dict], state: FindingState) -> dict | None:
    return next((e for e in reversed(events) if e["to_state"] == state and e.get("from_state") != state), None)


def _fix(events: list[dict]) -> tuple[str, int | None]:
    """The last fix commit recorded for a finding and its pass; a finding can close another way after one."""
    note = next((e["note"] for e in reversed(events) if e["note"].get("commit")), {})
    return note.get("commit", ""), note.get("pass")


def _decision_for(finding_id: str, decisions: list[dict]) -> dict | None:
    return next((d for d in reversed(decisions) if finding_id in d["finding_ids"]), None)


def _grouped(pairs: list[tuple[str, str]], acts: tuple[tuple[str, str], ...]) -> str:
    """Ids grouped by what happened to them, in the order the acts are listed: "verified A and B, withdrew C"."""
    groups = [f"{verb} {_join(_unique(fid for fid, key in pairs if key == wanted))}" for wanted, verb in acts
              if any(key == wanted for _, key in pairs)]
    return ", ".join(groups)


def _join(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else f"{', '.join(ids[:-1])} and {ids[-1]}"


def _unique(ids) -> list[str]:
    return list(dict.fromkeys(ids))


def _count(number: int, one: str, many: str) -> str:
    return "" if number == 0 else f"{number} {one if number == 1 else many}"


def _passes(count: int) -> str:
    return f"{count} pass{'es' if count != 1 else ''}"


def _check_words(status: str) -> str:
    return CHECK_WORDS.get(status, status.replace("_", " "))


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    return text if text.endswith((".", "?", "!")) else f"{text}."


def _first_sentence(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    match = re.search(r"[.?!](\s|$)", text)
    sentence = text[: match.end()].strip() if match else text
    if len(sentence) > limit:
        sentence = sentence[:limit].rsplit(" ", 1)[0] + "..."
    return _sentence(sentence)


def _quoted(text: str) -> str:
    return f'"{_first_sentence(text)}"'


def _source(decision: dict) -> str:
    return decision["source"].replace("_", " ")


def _elapsed(start: str, end: str) -> str:
    try:
        seconds = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except (TypeError, ValueError):
        return ""
    minutes = int(seconds // 60)
    if seconds < 0:
        return ""
    if minutes < 1:
        return "under a minute"
    days, hours, rest = minutes // 1440, minutes // 60 % 24, minutes % 60
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {rest}m" if hours else f"{rest}m"
