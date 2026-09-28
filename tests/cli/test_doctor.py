from pathlib import Path

from review_loop.cli.doctor import run_doctor
from tests.fakes.process import FakeProcessRunner

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
