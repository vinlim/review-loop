from review_loop.engine.findings import FindingState, Severity, fingerprint
from review_loop.engine.packet import PacketInput, render_packet
from review_loop.types.findings import Finding
from review_loop.types.pull_request import PullRef, PullRequest


def pull():
    return PullRequest(PullRef("acme", "webapp", 1004), "Fix the thing", "Because reasons.", "vinlim", "open", True, False,
                       "main", "b" * 40, "claude/x", "h" * 40)


def finding(id, state, severity="ISSUE"):
    return Finding(id=id, pass_no=1, severity=Severity(severity), state=state, file="app/X.php", line=7, symbol="f", title=f"Title {id}",
                   finding="Finding text", protected_behaviour="Guard holds", evidence="X.php:7", recommendation="Do Y",
                   proposed_refactor="code", fingerprint=fingerprint("app/X.php", "f", "t"), supersedes="", new_evidence="")


def test_the_packet_carries_every_section_the_phase_needs():
    text = render_packet(PacketInput(
        pull=pull(), merge_base_sha="m" * 40, instruction_files=["CLAUDE.md", "PROJECT.md"], discussion_text="### [reviewer] review #1\n\nbody",
        findings=[finding("R1-F1", FindingState.OPEN), finding("R1-F2", FindingState.VERIFIED)], events={"R1-F2": ["accepted by author: fixed in abc"]},
        decisions=["v1: notification failure does not roll back the order"], verification=["pass 1: .claude/run-tests.sh changed: passed (42 tests)"],
        phase="assess", active_ids=["R1-F1"], since_last_review=[], permitted_actions="Read, grep and read-only git; no edits.",
    ))

    assert "# Context packet: PR #1004" in text
    assert "Fix the thing" in text and "Because reasons." in text
    assert "merge base " + "m" * 40 in text
    assert "## Instruction files\n\nRead these in the checkout: CLAUDE.md, PROJECT.md" in text
    assert "## Discussion so far\n\n### [reviewer] review #1" in text
    assert "## Active findings" in text and "### R1-F1 [ISSUE] Title R1-F1" in text and "R1-F2" not in text.split("## Active findings")[1].split("## Ledger")[0]
    assert "## Ledger of prior findings" in text and "R1-F2 [ISSUE] verified" in text and "accepted by author: fixed in abc" in text
    assert "## Alignment decisions\n\n- v1: notification failure" in text
    assert "## Verification results\n\n- pass 1" in text
    assert "## Permitted actions in this phase\n\nRead, grep and read-only git; no edits." in text


def test_a_rereview_packet_lists_the_commits_since_the_last_review_and_no_active_section():
    text = render_packet(PacketInput(
        pull=pull(), merge_base_sha="m" * 40, instruction_files=[], discussion_text="(none)", findings=[], events={}, decisions=[],
        verification=[], phase="rereview", active_ids=[], since_last_review=["abc1234 fix: close the guard", "def5678 test: cover it"],
        permitted_actions="read only",
    ))

    assert "## Since your last review\n\n- abc1234 fix: close the guard\n- def5678 test: cover it" in text
    assert "## Active findings" not in text
    assert "(none)" in text.split("## Ledger of prior findings")[1]
