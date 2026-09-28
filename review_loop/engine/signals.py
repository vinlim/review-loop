"""Oscillation signals from the ledger and git, computed with no model call."""

from __future__ import annotations

from dataclasses import dataclass, field

from review_loop.engine.findings import FindingState
from review_loop.types.findings import Finding

REJECTED_STATES = {FindingState.REJECTED_PENDING_REVIEW, FindingState.REJECTION_ACCEPTED, FindingState.DISPUTED}


@dataclass(frozen=True)
class SignalInput:
    findings: list[Finding]
    events: dict[str, list[dict]]
    open_required_by_pass: dict[int, int]
    blob_hashes: dict[tuple[int, str], str]
    drift_by_pass: dict[int, list[str]]
    current_pass: int


@dataclass(frozen=True)
class Signal:
    kind: str
    finding_ids: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{','.join(self.finding_ids)}:{self.detail}"


def detect_signals(data: SignalInput) -> list[Signal]:
    signals: list[Signal] = []
    signals.extend(_re_raises(data))
    signals.extend(_reversals(data))
    signals.extend(_flip_flops(data))
    signals.extend(_no_convergence(data))
    signals.extend(_drift(data))
    return signals


def _re_raises(data: SignalInput) -> list[Signal]:
    by_id = {finding.id: finding for finding in data.findings}
    found = []
    for finding in data.findings:
        if finding.state != FindingState.OPEN or finding.new_evidence.strip():
            continue
        prior = by_id.get(finding.supersedes) or by_id.get(finding.possible_duplicate_of)
        if prior is not None and FindingState(prior.state) in REJECTED_STATES:
            found.append(Signal("re-raise", [finding.id], prior.id))
    return found


def _reversals(data: SignalInput) -> list[Signal]:
    found = []
    for finding_id, events in data.events.items():
        verified_seen = False
        for event in events:
            if event.get("actor") != "reviewer":
                continue
            if event.get("to_state") == FindingState.VERIFIED:
                verified_seen = True
            elif verified_seen and event.get("note", {}).get("resolution") == "disputed" and not event.get("note", {}).get("note", "").strip():
                found.append(Signal("reversal", [finding_id]))
                break
    return found


def _flip_flops(data: SignalInput) -> list[Signal]:
    if data.current_pass < 3:
        return []
    found = []
    files = {file for (_, file) in data.blob_hashes}
    for file in sorted(files):
        before = data.blob_hashes.get((data.current_pass - 2, file))
        middle = data.blob_hashes.get((data.current_pass - 1, file))
        after = data.blob_hashes.get((data.current_pass, file))
        if before and middle and after and before == after and middle != before:
            found.append(Signal("flip-flop", [], file))
    return found


def _no_convergence(data: SignalInput) -> list[Signal]:
    found = []
    counts = data.open_required_by_pass
    if data.current_pass >= 3 and all(n in counts for n in (data.current_pass - 2, data.current_pass - 1, data.current_pass)):
        a, b, c = (counts[data.current_pass - n] for n in (2, 1, 0))
        if a > 0 and b >= a and c >= b:
            found.append(Signal("no-convergence", [], f"open required findings {a}, {b}, {c}"))
    by_fingerprint: dict[str, set[int]] = {}
    for finding in data.findings:
        by_fingerprint.setdefault(finding.fingerprint, set()).add(finding.pass_no)
    for fingerprint_value, passes in by_fingerprint.items():
        if len(passes) >= 3:
            ids = [f.id for f in data.findings if f.fingerprint == fingerprint_value]
            found.append(Signal("no-convergence", ids, "same concern in three passes"))
    return found


def _drift(data: SignalInput) -> list[Signal]:
    current, previous = data.drift_by_pass.get(data.current_pass, []), data.drift_by_pass.get(data.current_pass - 1, [])
    if data.current_pass >= 2 and current and previous:
        return [Signal("drift", [], ", ".join(current))]
    return []
