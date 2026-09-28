"""review-loop: drive a reviewer agent and an author agent (Codex and Claude by default) through pull request review rounds."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_loop.cli.container import Container, build_container, default_home
from review_loop.cli.doctor import run_doctor
from review_loop.cli.register import append_registration, registration_toml
from review_loop.cli.render import render_show, render_status
from review_loop.config.settings import ConfigError
from review_loop.repositories import runs as runs_repo
from review_loop.services import run_control
from review_loop.services.start import start_run
from review_loop.types.run import PauseReason


def main(argv: list[str] | None = None, container: Container | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "repo":
        return command_repo_add(args)
    if args.command == "restore":
        return command_restore(args)
    try:
        box = container or build_container()
    except ConfigError as error:
        print(f"config error: {error}\nRegister a repository first: review-loop repo add <path>", file=sys.stderr)
        return 2
    return args.handler(args, box)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review-loop")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_setup_commands(commands)
    _add_run_commands(commands)
    _add_inbox_commands(commands)
    _add_state_commands(commands)
    return parser


def _add_setup_commands(commands) -> None:
    repo = commands.add_parser("repo", help="register a repository")
    repo_commands = repo.add_subparsers(dest="repo_command", required=True)
    add = repo_commands.add_parser("add", help="detect a repository's scripts and write its config table")
    add.add_argument("path")
    add.add_argument("--name", default="")
    add.add_argument("--remote", default="")
    add.add_argument("--author", default="")
    doctor = commands.add_parser("doctor", help="check CLIs, auth, schemas and config")
    doctor.set_defaults(handler=command_doctor)


def _add_run_commands(commands) -> None:
    start = commands.add_parser("start", help="enrol a pull request and run the loop")
    start.add_argument("url")
    start.add_argument("--author-session", default="auto")
    start.add_argument("--no-run", action="store_true", help="enrol only; do not run phases")
    start.add_argument("--inspect-only", action="store_true", help="review and assess into files; publish nothing")
    start.set_defaults(handler=command_start)
    status = commands.add_parser("status", help="list runs")
    status.set_defaults(handler=command_status)
    show = commands.add_parser("show", help="show one run")
    show.add_argument("run_id")
    show.set_defaults(handler=command_show)
    for name, handler in (("pause", command_pause), ("resume", command_resume), ("stop", command_stop)):
        sub = commands.add_parser(name)
        sub.add_argument("run_id")
        if name == "resume":
            sub.add_argument("--no-run", action="store_true")
        sub.set_defaults(handler=handler)
    align = commands.add_parser("align", help="optional override: settle disputed findings yourself")
    align.add_argument("run_id")
    align.add_argument("--file", required=True, help="a text file: first line `fix` or `keep`, then the reasoning")
    align.add_argument("--finding", action="append", default=[], help="finding id; repeatable")
    align.set_defaults(handler=command_align)


def _add_inbox_commands(commands) -> None:
    inbox = commands.add_parser("inbox", help="adjacent findings parked during reviews")
    inbox_commands = inbox.add_subparsers(dest="inbox_command", required=True)
    inbox_list = inbox_commands.add_parser("list")
    inbox_list.add_argument("--status", default="")
    inbox_list.set_defaults(handler=command_inbox_list)
    inbox_show = inbox_commands.add_parser("show")
    inbox_show.add_argument("item_id", type=int)
    inbox_show.set_defaults(handler=command_inbox_show)
    for name, status in (("dismiss", "dismissed"), ("schedule", "scheduled"), ("resolve", "resolved")):
        sub = inbox_commands.add_parser(name)
        sub.add_argument("item_id", type=int)
        sub.add_argument("--reason", default="")
        sub.add_argument("--reference", default="")
        sub.set_defaults(handler=command_inbox_status, status=status)


def _add_state_commands(commands) -> None:
    backup = commands.add_parser("backup", help="archive the state directory")
    backup.add_argument("--to", default="")
    backup.set_defaults(handler=command_backup)
    restore = commands.add_parser("restore", help="unpack an archive into an empty state directory")
    restore.add_argument("archive")
    restore.add_argument("--to", default="")


def command_repo_add(args) -> int:
    path = Path(args.path).resolve()
    name = args.name or path.name
    remote = args.remote or _detect_remote(path)
    author = args.author or _detect_login()
    home = default_home()
    registration = registration_toml(name, path, remote, home / "worktrees", author)
    append_registration(home / "config.toml", registration, state_dir=home)
    print(f"registered {name} in {home / 'config.toml'}; review it, then run: review-loop doctor")
    print(registration)
    return 0


def command_doctor(args, box: Container) -> int:
    checks = run_doctor(box.process, box.settings, box.schemas_dir)
    for check in checks:
        print(f"{'ok  ' if check.ok else 'FAIL'} {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def command_start(args, box: Container) -> int:
    from review_loop.engine.pull_url import parse_pull_url
    from review_loop.services.locks import AlreadyLocked, RunLock
    from review_loop.services.start import find_repository

    pull_ref = parse_pull_url(args.url)
    repo = find_repository(box.settings, pull_ref)
    if repo is None:
        print("refused: repository_not_registered", file=sys.stderr)
        return 2
    lock = RunLock(box.settings.state_dir, repo.name, pull_ref.number)
    try:
        lock.acquire()
    except AlreadyLocked as error:
        print(str(error), file=sys.stderr)
        return 3
    try:
        result = start_run(args.url, settings=box.settings, conn=box.conn, github=box.github, git=box.git, clock=box.clock,
                           versions=box.versions, author_session=args.author_session, inspect_only=args.inspect_only)
        if not result.ok:
            print(f"refused: {result.error.value}", file=sys.stderr)
            return 2
        run = result.value
        print(f"run {run.id} ({run.state.value}, {run.extra.get('mode', 'publish')}) for {run.pr_url}")
        if args.no_run:
            return 0
        return _drive(box, run, lock=lock)
    finally:
        lock.release()


def command_status(args, box: Container) -> int:
    print(render_status(runs_repo.list_runs(box.conn)))
    return 0


def command_show(args, box: Container) -> int:
    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    from review_loop.repositories import findings as findings_repo

    print(render_show(run, findings_repo.list_findings(box.conn, run.id)))
    return 0


def command_pause(args, box: Container) -> int:
    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    run_control.pause(box.conn, run, PauseReason.MANUAL, box.clock)
    print(f"paused {run.id}")
    return 0


def command_resume(args, box: Container) -> int:
    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    result = run_control.resume(box.conn, run, box.clock)
    if not result.ok:
        print(result.error, file=sys.stderr)
        return 2
    print(f"resumed {run.id} at {result.value.state.value}")
    if getattr(args, "no_run", False):
        return 0
    return _drive(box, result.value)


def command_stop(args, box: Container) -> int:
    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    run_control.stop(box.conn, run, box.clock)
    print(f"stopped {run.id}; the worktree and records are kept")
    return 0


def _drive(box: Container, run, lock=None) -> int:
    from review_loop.cli.wiring import build_deps
    from review_loop.services.locks import AlreadyLocked, RunLock
    from review_loop.services.run_coordinator import run_loop

    owned = lock is None
    lock = lock or RunLock(box.settings.state_dir, run.repo, run.pr_number)
    if owned:
        try:
            lock.acquire()
        except AlreadyLocked as error:
            print(str(error), file=sys.stderr)
            return 3
    try:
        final = run_loop(build_deps(box, inspect_only=run.extra.get("mode") == "inspect"), run, on_step=_print_transition)
    finally:
        if owned:
            lock.release()
    state = final.state.value + (f" ({final.pause_reason.value})" if final.pause_reason else "")
    outcome = f", outcome {final.outcome.value}" if final.outcome else ""
    print(f"run {final.id}: {state}{outcome}; artifacts under {box.settings.state_dir / 'runs' / final.id}")
    return 0 if final.state.value in ("complete",) else 1


def _print_transition(run) -> None:
    reason = f" ({run.pause_reason.value})" if run.pause_reason else ""
    print(f"{run.updated_at[:19]} pass {run.pass_no}: {run.state.value}{reason}", flush=True)


def command_align(args, box: Container) -> int:
    from review_loop.repositories import decisions as decisions_repo
    from review_loop.repositories import findings as findings_repo
    from review_loop.services.phase_support import apply_events
    from review_loop.cli.wiring import build_deps

    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    text = Path(args.file).read_text()
    kind = text.strip().splitlines()[0].strip().lower() if text.strip() else ""
    if kind not in ("fix", "keep"):
        print("the decision file must start with a line saying `fix` or `keep`", file=sys.stderr)
        return 2
    deps = build_deps(box, inspect_only=False)
    at = box.clock.now().isoformat()
    ids = args.finding or [f.id for f in findings_repo.list_findings(box.conn, run.id) if f.state in ("disputed", "needs_alignment")]
    decisions_repo.add_decision(box.conn, run.id, ids, "override", text, f"{kind}: developer override", {"kind": kind}, at)
    for fid in ids:
        finding = findings_repo.get_finding(box.conn, run.id, fid)
        if finding is None:
            continue
        events = ("align", "needs_alignment") if finding.state in ("disputed", "open") else ()
        for event in events:
            apply_events(deps, run, finding, (event,), "human", {"note": "developer override", "pass": run.pass_no}, at)
        apply_events(deps, run, finding, ("decide_fix",) if kind == "fix" else ("decide_keep",), "human", {"note": text.strip(), "pass": run.pass_no}, at)
    print(f"recorded a {kind} decision for {', '.join(ids)}; resume the run to continue")
    return 0


def command_backup(args, box: Container) -> int:
    from review_loop.services.backup import backup_state

    archive = backup_state(box.settings.state_dir, Path(args.to) if args.to else box.settings.state_dir.parent / "review-loop-backups", conn=box.conn)
    print(f"archived {box.settings.state_dir} to {archive}")
    return 0


def command_restore(args) -> int:
    """Runs before any configuration is loaded: the archive is what brings the configuration."""
    from review_loop.services.backup import restore_state

    target = restore_state(Path(args.archive), Path(args.to) if args.to else default_home())
    print(f"restored into {target}; run review-loop doctor next")
    return 0


def _require_run(box: Container, run_id: str):
    run = runs_repo.get_run(box.conn, run_id)
    if run is None:
        print(f"no run {run_id}; see review-loop status", file=sys.stderr)
    return run


def _detect_remote(path: Path) -> str:
    import subprocess

    completed = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _detect_login() -> str:
    import subprocess

    completed = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def command_inbox_list(args, box: Container) -> int:
    from review_loop.repositories import inbox as inbox_repo

    items = inbox_repo.list_items(box.conn, status=args.status or None)
    if not items:
        print("inbox is empty")
        return 0
    for item in items:
        location = f" {item['file']}" if item["file"] else ""
        print(f"#{item['id']} [{item['status']}] {item['repo']} PR #{item['source_pr']}{location}: {item['title']}")
    return 0


def command_inbox_show(args, box: Container) -> int:
    from review_loop.repositories import inbox as inbox_repo

    item = inbox_repo.get_item(box.conn, args.item_id)
    if item is None:
        print(f"no inbox item {args.item_id}", file=sys.stderr)
        return 2
    for key in ("id", "status", "repo", "source_pr", "source_commit", "source_run", "agent", "title", "description", "file", "symbol",
                "evidence", "impact", "next_step", "reference", "related", "created_at", "updated_at"):
        print(f"{key}: {item[key]}")
    return 0


def command_inbox_status(args, box: Container) -> int:
    from review_loop.repositories import inbox as inbox_repo

    if inbox_repo.get_item(box.conn, args.item_id) is None:
        print(f"no inbox item {args.item_id}", file=sys.stderr)
        return 2
    inbox_repo.set_status(box.conn, args.item_id, args.status, args.reference or args.reason, box.clock.now().isoformat())
    print(f"#{args.item_id} {args.status}")
    return 0
