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


def test_inspect_only_probes_for_an_existing_run(skill):
    starting = section(skill, "## 4. Starting a run")
    assert "--no-run --inspect-only" in starting
    assert starting.index("--no-run --inspect-only") < starting.index("\"dry run\"")


def test_align_recipe_checks_state_before_resume(skill):
    align = section(skill, "## 9. Align")
    assert "show <run-id>" in align
    assert "blocked" in align
    assert align.index("show <run-id>") < align.index("review-loop resume")


def test_prepare_failed_row_names_no_log(skill):
    row = next(line for line in skill.splitlines() if line.startswith("| `prepare_failed`"))
    assert "log" not in row
    assert "prepare" in row.split("|")[3]
