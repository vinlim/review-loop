"""The agent skill describes the CLI as it is; these checks pin the instructions that guard publication."""

from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2] / "skills" / "review-loop" / "SKILL.md"


@pytest.fixture
def skill() -> str:
    return SKILL.read_text()


def section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def test_explicit_operations_route_before_bare_identifiers(skill):
    cascade = section(skill, "## 2. Pick the action")
    control = cascade.index("pause")
    assert cascade.index("Align") < cascade.index("bare run id")
    assert control < cascade.index("bare run id")
    assert control < cascade.index("bare PR URL")


def test_start_carries_the_requested_mode_and_never_probes(skill):
    starting = section(skill, "## 4. Starting a run")
    assert "--no-run" not in starting, "a separate probe is not a safety boundary; start refuses a mode conflict itself"
    assert starting.index("\"dry run\"") < starting.index("review-loop start"), "the requested mode is decided before the command"
    assert "mode_conflict" in starting


def test_exit_code_zero_covers_a_blocked_outcome(skill):
    row = next(line for line in skill.splitlines() if line.startswith("| 0 |"))
    assert "blocked" in row
    row = next(line for line in skill.splitlines() if line.startswith("| 1 |"))
    assert "blocked" not in row


def test_inspect_only_pause_row_forbids_resume(skill):
    row = next(line for line in skill.splitlines() if line.startswith("| `inspect_only`"))
    assert "Do not `resume`" in row


def test_align_recipe_checks_state_before_resume(skill):
    align = section(skill, "## 9. Align")
    assert "show <run-id>" in align
    assert "blocked" in align
    assert align.index("show <run-id>") < align.index("review-loop resume")


def test_check_environment_withholds_the_claude_login(skill):
    """The coordinator claims the Claude login at startup; only the Claude agent's own process receives it."""
    onboarding = " ".join(section(skill, "## 3. Onboarding").split())
    assert "token stays" not in onboarding
    sentence = next(part for part in onboarding.split(". ") if "CLAUDE_CODE_OAUTH_TOKEN" in part)
    assert "only to the Claude agent" in sentence
    assert "no check" in sentence


def test_prepare_failed_row_reads_the_prepare_log_first(skill):
    row = next(line for line in skill.splitlines() if line.startswith("| `prepare_failed`"))
    action = row.split("|")[3]
    assert "not kept" not in action
    assert "`show <run-id>`" in action and "prepare-<n>.log" in action
    assert action.index("show") < action.index("workspace.prepare"), "rerunning the commands by hand is the fallback"
