"""Readiness checks: every hard dependency, with the fix in the message when it is missing."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from review_loop.config.settings import Settings
from review_loop.services.workspace import is_pytest_check
from review_loop.services.state_dir import readable_by_others
from review_loop.types.agents import AGENT_PROFILES
from review_loop.types.protocols import ProcessRunner

EXPECTED_SCHEMAS = ("review", "assessment", "fix", "alignment", "arbitration")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_doctor(process: ProcessRunner, settings: Settings, schemas_dir: Path) -> list[Check]:
    agents = settings.agents()
    checks = [_python(), *(_agent(process, name) for name in agents), *([_codex_login(process)] if "codex" in agents else []),
              _gh(process), _schemas(schemas_dir), _config(settings), _state_dir(settings)]
    checks.extend(_repository(repo) for repo in settings.repositories.values())
    checks.extend(_verification(process, repo) for repo in settings.repositories.values())
    checks.extend(_worktree_config(process, repo) for repo in settings.repositories.values())
    return checks


def _python() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = sys.version_info >= (3, 11)
    return Check("python", ok, version if ok else f"{version} is too old; review-loop needs Python 3.11 or newer")


def _agent(process: ProcessRunner, name: str) -> Check:
    profile = AGENT_PROFILES[name]
    completed = _run(process, [profile.binary, "--version"])
    if completed.exit_code != 0:
        return Check(name, False, f"{profile.binary} not found on PATH; {profile.install_hint}")
    return Check(name, True, completed.stdout.strip())


def _codex_login(process: ProcessRunner) -> Check:
    completed = _run(process, ["codex", "login", "status"])
    text = (completed.stdout + completed.stderr).strip()
    ok = completed.exit_code == 0 and "Logged in" in text
    detail = text if ok else "codex is not logged in; run `codex login` (or `codex login --device-auth` on a headless host)"
    return Check("codex login", ok, detail)


def _gh(process: ProcessRunner) -> Check:
    completed = _run(process, ["gh", "auth", "status"])
    ok = completed.exit_code == 0
    return Check("gh", ok, completed.stdout.strip() if ok else "gh is not authenticated; run `gh auth login`")


def _schemas(schemas_dir: Path) -> Check:
    missing = [name for name in EXPECTED_SCHEMAS if not (schemas_dir / f"{name}.json").exists()]
    if missing:
        return Check("schemas", False, f"missing schema files in {schemas_dir}: {', '.join(missing)}")
    for name in EXPECTED_SCHEMAS:
        try:
            jsonschema.Draft7Validator.check_schema(json.loads((schemas_dir / f"{name}.json").read_text()))
        except (jsonschema.SchemaError, json.JSONDecodeError) as error:
            return Check("schemas", False, f"{name}.json is not a valid draft-07 schema: {error}")
    return Check("schemas", True, f"{len(EXPECTED_SCHEMAS)} files valid in {schemas_dir}")


def _config(settings: Settings) -> Check:
    return Check("config", True, f"{settings.source}: repositories {', '.join(settings.repositories)}")


def _state_dir(settings: Settings) -> Check:
    if not settings.state_dir.exists():
        return Check("state directory", False, f"{settings.state_dir} does not exist")
    if readable_by_others(settings.state_dir):
        return Check("state directory", False, f"other accounts can read {settings.state_dir}, which holds every captured log and the database; "
                                               f"run `chmod 700 {settings.state_dir}`")
    return Check("state directory", True, f"{settings.state_dir} is private to this account")


def _repository(repo) -> Check:
    missing = []
    for command in repo.workspace.prepare + repo.verification.required + repo.verification.format:
        for argument in command[:2]:
            if "/" in argument and not (repo.local_path / argument).exists():
                missing.append(argument)
    if not repo.local_path.exists():
        return Check(f"repository {repo.name}", False, f"local_path {repo.local_path} does not exist")
    if missing:
        return Check(f"repository {repo.name}", False, f"registered commands reference files missing under {repo.local_path}: {', '.join(missing)}")
    return Check(f"repository {repo.name}", True, f"{repo.local_path}")


def _verification(process: ProcessRunner, repo) -> Check:
    name = f"repository {repo.name} verification"
    if not repo.verification.required:
        return Check(name, False, f"no required check; a fix can never be verified. Add `required` under `[repositories.{repo.name}.verification]`")
    for command in repo.verification.required:
        if is_pytest_check(command) and _run(process, [command[0], "-c", "import pytest"]).exit_code != 0:
            return Check(name, False, f"{command[0]} cannot import pytest; install it with `{command[0]} -m pip install pytest`")
    return Check(name, True, "; ".join(" ".join(command) for command in repo.verification.required))


def _worktree_config(process: ProcessRunner, repo) -> Check:
    completed = _run(process, ["git", "-C", str(repo.local_path), "config", "--local", "--type=bool", "--default=false", "--get",
                               "extensions.worktreeConfig"])
    if completed.exit_code == 0 and completed.stdout.strip() == "true":
        return Check(f"repository {repo.name} worktree config", True, "extensions.worktreeConfig is on")
    return Check(f"repository {repo.name} worktree config", False,
                 "extensions.worktreeConfig is off, so the push lock cannot be kept to the tool's worktrees; turn it on once with "
                 f"`git -C {repo.local_path} config extensions.worktreeConfig true`")


def _run(process: ProcessRunner, argv: list[str]):
    import os

    return process.run(argv, cwd=str(Path.home()), env=dict(os.environ), timeout_seconds=60)
