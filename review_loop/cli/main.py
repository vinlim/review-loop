"""review-loop: drive a reviewer agent and an author agent (Codex and Claude by default) through pull request review rounds."""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

from review_loop.cli.container import Container, build_container, claim_claude_oauth_token, default_home
from review_loop.cli.doctor import run_doctor
from review_loop.cli.register import append_registration, registration_toml
from review_loop.cli.render import render_show, render_status
from review_loop.config.settings import ConfigError
from review_loop.repositories import runs as runs_repo
from review_loop.services import run_control
from review_loop.services.locks import AlreadyLocked, RunLock
from review_loop.services.start import StartRefusal, requested_mode, start_run
from review_loop.types.result import Err, Ok, Result
from review_loop.types.run import WORKING_STATES, PauseReason


def main(argv: list[str] | None = None, container: Container | None = None) -> int:
    claude_oauth_token = claim_claude_oauth_token()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "repo":
        return command_repo_add(args)
    if args.command == "restore":
        return command_restore(args)
    try:
        box = container or build_container(claude_oauth_token=claude_oauth_token)
    except ConfigError as error:
        print(f"config error: {error}", file=sys.stderr)
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
    doctor.add_argument("--no-probe", action="store_true", help="skip the one-request probe of each configured agent")
    doctor.set_defaults(handler=command_doctor)


def _add_run_commands(commands) -> None:
    start = commands.add_parser("start", help="enrol a pull request and run the loop")
    start.add_argument("url")
    start.add_argument("--author-session", default="auto")
    start.add_argument("--no-run", action="store_true", help="enrol only; do not run phases")
    start.add_argument("--inspect-only", action="store_true", help="review and assess into files; publish nothing")
    start.add_argument("--detach", action="store_true", help="hand the run to a coordinator in its own session and return")
    start.add_argument("--no-preflight", action="store_true", help="skip the probe of each agent before the first phase")
    start.set_defaults(handler=command_start)
    drive = commands.add_parser("drive", help="drive an enrolled run in this process (what --detach starts)")
    drive.add_argument("run_id")
    drive.add_argument("--no-preflight", action="store_true", help="skip the probe of each agent; --detach passes this, having probed")
    drive.set_defaults(handler=command_drive)
    status = commands.add_parser("status", help="list runs")
    status.set_defaults(handler=command_status)
    wait = commands.add_parser("wait", help="follow a run another process drives until it stops")
    wait.add_argument("run_id")
    wait.add_argument("--interval", type=float, default=3.0, help="seconds between polls")
    wait.set_defaults(handler=command_wait)
    show = commands.add_parser("show", help="show one run")
    show.add_argument("run_id")
    show.set_defaults(handler=command_show)
    for name, handler in (("pause", command_pause), ("resume", command_resume), ("stop", command_stop)):
        sub = commands.add_parser(name)
        sub.add_argument("run_id")
        if name == "resume":
            sub.add_argument("--no-run", action="store_true")
            sub.add_argument("--detach", action="store_true", help="hand the run to a coordinator in its own session and return")
            sub.add_argument("--no-preflight", action="store_true", help="skip the probe of each agent before continuing")
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
    from review_loop.cli.wiring import build_deps
    from review_loop.services.preflight import Probing

    probing = None if args.no_probe else Probing(agents=build_deps(box, inspect_only=False).agents, base_env=dict(os.environ),
                                                 output_dir=box.settings.state_dir / "doctor")
    checks = run_doctor(box.process, box.settings, box.schemas_dir, probing=probing)
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
            print(_refusal_line(box, repo.name, pull_ref.number, result.error, args.inspect_only), file=sys.stderr)
            return 2
        run = result.value
        print(f"run {run.id} ({run.state.value}, {run.mode()}) for {run.pr_url}")
        if args.no_run:
            return 0
        if not args.no_preflight and _preflight(box, run):
            return 1
        if not args.detach:
            return _drive(box, run, lock)
    finally:
        lock.release()
    return _detach(box, run)


def _preflight(box: Container, run) -> bool:
    """Probe the run's agents before any phase; a failure pauses the run with the answers and returns True."""
    from review_loop.cli.wiring import build_deps
    from review_loop.services.preflight import failures, probe_agents

    deps = build_deps(box, inspect_only=run.mode() == "inspect")
    probes = probe_agents(deps.agents, box.settings.repositories[run.repo], state_dir=box.settings.state_dir, base_env=deps.base_env,
                          output_dir=box.settings.state_dir / "runs" / run.id / "preflight", process=box.process)
    failed = failures(probes)
    if not failed:
        return False
    run.extra["preflight_failure"] = failed
    _report_final(box, run_control.pause(box.conn, run, PauseReason.AGENT_UNAVAILABLE, box.clock))
    return True


def _detach(box: Container, run) -> int:
    """A coordinator in its own session takes the run; the lock is free by now so the child can hold it. The claimed
    Claude login travels only to that child, which claims it again for its own Claude adapter."""
    log_path = box.settings.state_dir / "runs" / run.id / "coordinator.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **({"CLAUDE_CODE_OAUTH_TOKEN": box.claude_oauth_token} if box.claude_oauth_token else {})}
    argv = [sys.executable, "-m", "review_loop.cli.main", "drive", run.id, "--no-preflight"]  # this process probed, or was told not to
    pid = box.spawn(argv, cwd=str(box.home), env=env, log_path=log_path)
    print(f"coordinator pid {pid} drives it in its own session; log: {log_path}; follow with: review-loop wait {run.id}")
    return 0


def command_drive(args, box: Container) -> int:
    claimed = _claim(box, args.run_id)
    if not claimed.ok:
        return claimed.error
    run, lock = claimed.value
    try:
        if run.state not in WORKING_STATES:
            hint = f"; resume it with `review-loop resume {run.id}`" if run.state.value == "paused" else ""
            print(f"run {run.id} is {run.state.value}, not in a working state{hint}", file=sys.stderr)
            return 2
        if not args.no_preflight and _preflight(box, run):
            return 1
        return _drive(box, run, lock)
    finally:
        lock.release()


def _claim(box: Container, run_id: str) -> Result[tuple, int]:
    """The run as it stands once this process holds its lock, with the lock. What was read before the lock may have moved
    or finished under another coordinator, so only the copy read after counts. The error is the exit code, already explained."""
    run = _require_run(box, run_id)
    if run is None:
        return Err(2)
    lock = _lock(box, run)
    try:
        lock.acquire()
    except AlreadyLocked as error:
        print(f"{error}; a coordinator is driving run {run.id}, follow it with `review-loop wait {run.id}`", file=sys.stderr)
        return Err(3)
    return Ok((runs_repo.get_run(box.conn, run_id), lock))


def _refusal_line(box: Container, repo_name: str, pr_number: int, refusal, inspect_only: bool) -> str:
    """A mode conflict names the run in the way; the operator has to stop it before any start in the other mode works."""
    if refusal != StartRefusal.MODE_CONFLICT:
        return f"refused: {refusal.value}"
    active = runs_repo.find_active_run(box.conn, repo_name, pr_number)
    mode = active.mode() if active else "another"
    return (f"refused: mode_conflict (run {active.id if active else '?'} is {mode}; "
            f"stop it before starting in {requested_mode(inspect_only)} mode)")


def command_status(args, box: Container) -> int:
    runs = runs_repo.list_runs(box.conn)
    print(render_status(runs, unattended={run.id for run in runs if _unattended(box, run)}))
    return 0


def command_show(args, box: Container) -> int:
    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    from review_loop.repositories import findings as findings_repo

    repo = box.settings.repositories.get(run.repo)
    print(render_show(run, findings_repo.list_findings(box.conn, run.id), configured=repo.review.agents() if repo else None,
                      unattended=_unattended(box, run)))
    return 0


def command_wait(args, box: Container) -> int:
    from review_loop.services.wait import wait_for_run

    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    final = wait_for_run(box.conn, run.id, lock_held=lambda current: _lock(box, current).is_held(), sleep=time.sleep,
                         on_step=_print_transition, interval_seconds=args.interval)
    return _report_final(box, final)


def _head_unchanged(box: Container, run) -> bool:
    """For a head_changed pause: whether git shows the branch still where the run left it, in which case the pause was
    the API lagging a push and the run can continue where it paused instead of preparing again."""
    from review_loop.cli.wiring import build_deps
    from review_loop.services.phase_support import head_moved

    if run.pause_reason != PauseReason.HEAD_CHANGED:
        return False
    return not head_moved(build_deps(box, inspect_only=run.mode() == "inspect"), run)


def _lock(box: Container, run) -> RunLock:
    return RunLock(box.settings.state_dir, run.repo, run.pr_number)


def _unattended(box: Container, run) -> bool:
    """A run a coordinator should be driving, with no coordinator holding its lock."""
    return run.state in WORKING_STATES and not _lock(box, run).is_held()


def command_pause(args, box: Container) -> int:
    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    result = run_control.pause_by_hand(box.conn, run, box.clock)
    if not result.ok:
        print(f"run {run.id} is {result.error.state.value}; a finished run cannot be paused", file=sys.stderr)
        return 2
    print(f"paused {run.id}")
    return 0


def command_resume(args, box: Container) -> int:
    claimed = _claim(box, args.run_id)
    if not claimed.ok:
        return claimed.error
    run, lock = claimed.value
    try:
        # With the lock held, a working state means no coordinator has this run: it continues from that state.
        result = run_control.resume(box.conn, run, box.clock, unattended=run.state in WORKING_STATES,
                                    head_unchanged=_head_unchanged(box, run))
        if not result.ok:
            print(result.error, file=sys.stderr)
            return 2
        print(f"resumed {run.id} at {result.value.state.value}")
        if args.no_run:
            return 0
        if not args.no_preflight and _preflight(box, result.value):
            return 1
        if not args.detach:
            return _drive(box, result.value, lock)
    finally:
        lock.release()
    return _detach(box, result.value)


def command_stop(args, box: Container) -> int:
    run = _require_run(box, args.run_id)
    if run is None:
        return 2
    result = run_control.stop(box.conn, run, box.clock)
    if not result.ok:
        print(f"run {run.id} is {result.error.state.value}; a finished run cannot be stopped", file=sys.stderr)
        return 2
    print(f"stopped {run.id}; the worktree and records are kept")
    return 0


def _drive(box: Container, run, lock: RunLock) -> int:
    """Run the loop on a run this process has claimed; `lock` is held by the caller for the whole drive."""
    from review_loop.cli.wiring import build_deps
    from review_loop.services.run_coordinator import run_loop

    try:
        final = run_loop(build_deps(box, inspect_only=run.mode() == "inspect"), run, on_step=_print_transition)
    except Exception:  # noqa: BLE001 - a crash of any kind must leave a resumable, explained run behind
        final = _pause_after_crash(box, run)
    return _report_final(box, final)


def _pause_after_crash(box: Container, run):
    """The run stays where its last persisted transition left it, paused with the traceback for show; resume retries that phase."""
    traceback.print_exc(file=sys.stderr)
    current = runs_repo.get_run(box.conn, run.id) or run
    current.extra["coordinator_failure"] = traceback.format_exc()[-4000:]
    return run_control.pause(box.conn, current, PauseReason.COORDINATOR_FAILED, box.clock)


def _report_final(box: Container, final) -> int:
    """The closing line of a drive or a wait: the state the run is in, and 0 only for a complete run."""
    state = final.state.value + (f" ({final.pause_reason.value})" if final.pause_reason else "")
    if _unattended(box, final):
        state += " (no coordinator)"
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


if __name__ == "__main__":
    raise SystemExit(main())
