from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    id: str
    pass_no: int
    severity: str
    state: str
    file: str
    line: int
    symbol: str
    title: str
    finding: str
    protected_behaviour: str
    evidence: str
    recommendation: str
    proposed_refactor: str
    fingerprint: str
    supersedes: str = ""
    new_evidence: str = ""
    possible_duplicate_of: str = ""
    review_id: int | None = None
    comment_id: int | None = None
    thread_id: str = ""
    blocking: bool = True
    extra: dict[str, str] = field(default_factory=dict)
