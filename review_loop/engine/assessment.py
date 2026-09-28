from __future__ import annotations

from review_loop.types.result import Err, Ok, Result


def validate_dispositions(active_ids: list[str], dispositions: list[dict], severities: dict[str, str] | None = None) -> Result[None, str]:
    """Exactly one disposition per active finding, and `answer` only closes a QUESTION."""
    severities = severities or {}
    seen: set[str] = set()
    for disposition in dispositions:
        finding_id = disposition["finding_id"]
        if finding_id not in active_ids:
            return Err(f"disposition for {finding_id}, which is not an active finding")
        if finding_id in seen:
            return Err(f"{finding_id} is dispositioned twice")
        if disposition["disposition"] == "answer" and severities.get(finding_id, "QUESTION") != "QUESTION":
            return Err(f"{finding_id} is a {severities[finding_id]}; `answer` only closes a QUESTION")
        seen.add(finding_id)
    missing = [finding_id for finding_id in active_ids if finding_id not in seen]
    if missing:
        return Err(f"no disposition for {', '.join(missing)}")
    return Ok(None)


def validate_review_coverage(review: dict, pending_blocker_ids: list[str]) -> Result[None, str]:
    """A rereview must say something about every pending BLOCKER; silence closes lesser findings, never a blocker."""
    mentioned = {entry["id"] for entry in review.get("resolved_prior", [])}
    missing = [fid for fid in pending_blocker_ids if fid not in mentioned]
    if missing:
        return Err(f"resolved_prior says nothing about pending BLOCKER {', '.join(missing)}")
    return Ok(None)
