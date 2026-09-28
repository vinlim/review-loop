import dataclasses
from pathlib import Path

from review_loop.cli.container import TOOL_VERSION, tool_versions
from review_loop.cli.doctor import run_doctor
from tests.fakes.process import FakeProcessRunner
from tests.services.test_coordinator_phases import with_review

SCHEMAS = Path(__file__).resolve().parents[2] / "review_loop" / "schemas"


def make(codex_present=True, worktree_config="true"):
    process = FakeProcessRunner()
    process.script(["git"], stdout=f"{worktree_config}\n")
    process.script(["claude", "--version"], stdout="2.1.278 (Claude Code)\n")
    if codex_present:
        process.script(["codex", "--version"], stdout="codex-cli 0.157.1\n")
        process.script(["codex", "login", "status"], stderr="Logged in using ChatGPT\n")
    else:
        process.script(["codex", "--version"], exit_code=127, stderr="not found: codex")
        process.script(["codex", "login", "status"], exit_code=127, stderr="not found: codex")
    process.script(["gh", "auth", "status"], stdout="github.com\n  Logged in to github.com account vinlim\n")
    return process


def test_doctor_reports_every_dependency_with_its_version(settings):
    checks = {check.name: check for check in run_doctor(make(), settings, SCHEMAS)}

    assert checks["claude"].ok and "2.1.278" in checks["claude"].detail
    assert checks["codex"].ok and "0.157.1" in checks["codex"].detail
    assert checks["codex login"].ok and "ChatGPT" in checks["codex login"].detail
    assert checks["gh"].ok
    assert checks["schemas"].ok and "5 files" in checks["schemas"].detail
    assert checks["python"].ok
    assert checks["config"].ok and "webapp" in checks["config"].detail
    assert checks["repository webapp worktree config"].ok


def test_doctor_fails_a_missing_codex_with_the_install_command(settings):
    checks = {check.name: check for check in run_doctor(make(codex_present=False), settings, SCHEMAS)}

    assert not checks["codex"].ok
    assert "npm i -g @openai/codex" in checks["codex"].detail
    assert not checks["codex login"].ok


def test_doctor_fails_a_repository_without_per_worktree_config_with_the_command_to_turn_it_on(settings):
    checks = {check.name: check for check in run_doctor(make(worktree_config="false"), settings, SCHEMAS)}

    check = checks["repository webapp worktree config"]
    assert not check.ok
    assert f"git -C {settings.repositories['webapp'].local_path} config extensions.worktreeConfig true" in check.detail


def test_doctor_fails_when_a_registered_prepare_script_is_missing(settings, tmp_path):
    checks = {check.name: check for check in run_doctor(make(), settings, SCHEMAS)}

    assert not checks["repository webapp"].ok
    assert ".claude/worktree-setup.sh" in checks["repository webapp"].detail


def with_required(settings, required):
    repo = settings.repositories["webapp"]
    return dataclasses.replace(settings, repositories={"webapp": dataclasses.replace(repo, verification=dataclasses.replace(repo.verification, required=required))})


def test_doctor_fails_a_repository_with_no_required_check_and_names_the_table_to_fill(settings):
    checks = {check.name: check for check in run_doctor(make(), with_required(settings, []), SCHEMAS)}

    check = checks["repository webapp verification"]
    assert not check.ok
    assert "a fix can never be verified" in check.detail and "[repositories.webapp.verification]" in check.detail


def test_doctor_fails_a_pytest_check_whose_interpreter_cannot_import_pytest(settings):
    process = make()
    process.script(["/opt/venv/bin/python", "-c", "import pytest"], exit_code=1, stderr="ModuleNotFoundError: No module named 'pytest'")

    checks = {check.name: check for check in run_doctor(process, with_required(settings, [["/opt/venv/bin/python", "-m", "pytest", "-q"]]), SCHEMAS)}

    assert not checks["repository webapp verification"].ok
    assert "/opt/venv/bin/python -m pip install pytest" in checks["repository webapp verification"].detail


def test_doctor_passes_a_pytest_check_whose_interpreter_has_pytest(settings):
    process = make()
    process.script(["/opt/venv/bin/python", "-c", "import pytest"])

    checks = {check.name: check for check in run_doctor(process, with_required(settings, [["/opt/venv/bin/python", "-m", "pytest", "-q"]]), SCHEMAS)}

    assert checks["repository webapp verification"].ok and "-m pytest -q" in checks["repository webapp verification"].detail


def test_doctor_lists_the_required_checks_when_there_are_some(settings):
    checks = {check.name: check for check in run_doctor(make(), settings, SCHEMAS)}

    assert checks["repository webapp verification"].ok and ".claude/run-tests.sh changed" in checks["repository webapp verification"].detail


def test_doctor_checks_only_the_agents_the_repositories_configure(settings):
    process = FakeProcessRunner()
    process.script(["git"], stdout="true\n")
    process.script(["agy", "--version"], stdout="agy 1.4.0\n")
    process.script(["claude", "--version"], stdout="2.1.278 (Claude Code)\n")
    process.script(["gh", "auth", "status"], stdout="Logged in\n")

    checks = {check.name: check for check in run_doctor(process, with_review(settings, reviewer="agy"), SCHEMAS)}

    assert checks["agy"].ok and "1.4.0" in checks["agy"].detail and checks["claude"].ok
    assert "codex" not in checks and "codex login" not in checks


def test_doctor_fails_a_missing_opencode_with_the_install_command(settings):
    process = make()
    process.script(["opencode", "--version"], exit_code=127, stderr="not found: opencode")

    checks = {check.name: check for check in run_doctor(process, with_review(settings, author="opencode"), SCHEMAS)}

    assert not checks["opencode"].ok and "npm i -g opencode-ai" in checks["opencode"].detail


def test_the_version_probe_asks_each_configured_agent_and_gh(settings):
    process = FakeProcessRunner()
    for tool in ("agy", "claude", "gh"):
        process.script([tool, "--version"], stdout=f"{tool} 1.0\n")

    versions = tool_versions(process, {}, with_review(settings, reviewer="agy"))

    assert versions == {"review-loop": TOOL_VERSION, "agy": "agy 1.0", "claude": "claude 1.0", "gh": "gh 1.0"}


def test_doctor_fails_a_state_directory_other_accounts_can_read_with_the_chmod_fix(settings):
    import os

    settings.state_dir.mkdir(parents=True)
    os.chmod(settings.state_dir, 0o755)

    check = {check.name: check for check in run_doctor(make(), settings, SCHEMAS)}["state directory"]

    assert not check.ok and f"chmod 700 {settings.state_dir}" in check.detail


def test_doctor_passes_a_private_state_directory(settings):
    import os

    settings.state_dir.mkdir(parents=True)
    os.chmod(settings.state_dir, 0o700)

    check = {check.name: check for check in run_doctor(make(), settings, SCHEMAS)}["state directory"]

    assert check.ok
