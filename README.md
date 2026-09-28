# review-loop

Drives a Codex reviewer and a Claude author through pull request review rounds: review, critical
assessment, scoped fixes, coordinator-run checks, guarded push, replies, rereview, and alignment with
blind arbitration when the two disagree. The developer reads one report at merge time. The design
and the engineering plan are in `PLAN.md`.

## Install (Mac)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
npm i -g @openai/codex            # uses the ChatGPT desktop login at ~/.codex/auth.json
.venv/bin/review-loop repo add ~/code/your-app
.venv/bin/review-loop doctor
```

At the end of a run the tool posts one report to the PR: an overview, how each finding ended, every
exception with both positions, the findings closed without a verified fix and why, the run pass by pass, and the
commits it made with their files. The report is built from the run's records, so its ids, commits and
counts are exact. The same text is saved as `runs/<run-id>/report.md`.

`repo add` writes `~/.review-loop/config.toml`. Everything the tool produces lives under
`~/.review-loop/`: `state.db`, `runs/<run-id>/` (packets, prompts, agent outputs, verification logs,
`report.md`), `worktrees/`, `logs/`.

A registered repository needs per-worktree config, because each tool worktree keeps its push lock in
its own config file, out of the shared `.git/config` every checkout reads. Turn it on once with
`git -C <checkout> config extensions.worktreeConfig true`; `doctor` checks it.

## Use

```bash
review-loop start https://github.com/<owner>/<repo>/pull/<n>              # the loop, end to end
review-loop start <pr url> --inspect-only                                  # review and assess into files; publish nothing
review-loop status | show <run-id> | pause <run-id> | resume <run-id> | stop <run-id>
review-loop inbox list | show <id> | dismiss <id> --reason … | schedule <id> --reference … | resolve <id> --reference …
review-loop align <run-id> --file decision.md                              # optional override, never required
review-loop backup | restore <archive>
```

Inspect-only is a property of the run, fixed at `start`: a dry run can never be resumed into a fix or a
push. To run the same PR for real, `stop` it and `start` again (the worktree is reused); until then a
`start` in the other mode is refused with `mode_conflict`. The three
`publication` switches in the config (`post_reviews`, `post_author_responses`, `push_verified_fixes`)
are honoured at the point of each effect.

A run pauses, never guesses, on: a usage limit, expired auth, a remote head that moved, a dirty or
foreign worktree, a PR or fix that changes a registered script, checks that fail twice or cannot run
(a timed-out check counts as unavailable), a commit in the worktree the coordinator did not make, a
push the remote does not confirm, a GitHub error, or an agent that returns nothing usable. `resume`
continues from the paused phase; after a moved head it goes back through preparation. `stop` and
`pause` from another terminal take effect before the running loop's next phase.

Agents run with no tokens, no credential helpers (`GIT_CONFIG_GLOBAL` empty, `GIT_CONFIG_NOSYSTEM`,
`GH_CONFIG_DIR` empty), pushes disabled through `GIT_CONFIG_*` variables, and a `gh` shim on PATH.
The developer's own git configuration is never touched. Everything posted to GitHub and every
commit message passes through the attribution filter; the author's desktop session is forked, never
appended to.

## Agent skill

`skills/review-loop/SKILL.md` teaches a coding agent (Claude Code, or anything that reads agent
skills) to operate this tool: start and inspect runs, act on each pause reason, read the report,
and work the inbox, without doing the review or the fixes itself. Install it for yourself by
copying or linking the directory into your skills folder:

```bash
ln -s "$PWD/skills/review-loop" ~/.claude/skills/review-loop
```

Then ask the agent to run review-loop on a PR URL, check a run, or open the inbox.

## Verify

```bash
.venv/bin/python -m pytest -q            # offline, with fakes for git, GitHub and both agents
REVIEW_LOOP_LIVE_PR=owner/repo#123 .venv/bin/python -m pytest -q -m live    # opt-in: the real CLIs
```

## Cloud

`Dockerfile` and `docker-compose.yml` hold the VPS shape: the same coordinator, the PHP and Node
toolchain, the state directory as a volume, `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`,
`codex login --device-auth` for the Codex store, and `GH_TOKEN` for GitHub. `backup` and `restore`
move the state directory between hosts; worktrees are rebuilt from the registered checkout.
