# review-loop

Drives a reviewer agent and an author agent (Codex and Claude by default) through pull request review
rounds: review, critical assessment, scoped fixes, coordinator-run checks, guarded push, replies, rereview, and alignment with
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

`repo add` writes `~/.review-loop/config.toml`. It registers `.claude/run-tests.sh changed` as the
required check when the project has one, with its `full` mode as the fallback, or pytest when the
project configures it; a registration with no required check fails `doctor`, because a fix can never
be verified without one. A required check that exits with a code in `unavailable_exit_codes` selected
nothing to run. When every required check passed or selected nothing, the `verification.fallback`
commands run and their result stands in place of the required ones; a failure or a timeout never hands
over, and a check that ran nothing is never a pass. A pytest check runs with the
worktree, and the import roots its pytest configuration declares, leading `PYTHONPATH`, so the processes it
starts resolve the project as pytest does: from the worktree. Everything the tool produces lives under
`~/.review-loop/`: `state.db`, `runs/<run-id>/` (packets, prompts, agent outputs, prepare and
verification logs, `report.md`), `worktrees/`, `logs/`. The directory is created private to your
account, and `doctor` fails when other accounts can read it.

A registered repository needs per-worktree config, because each tool worktree keeps its push lock in
its own config file, out of the shared `.git/config` every checkout reads. Turn it on once with
`git -C <checkout> config extensions.worktreeConfig true`; `doctor` checks it.

## Use

```bash
review-loop start https://github.com/<owner>/<repo>/pull/<n>              # the loop, end to end, in this process
review-loop start <pr url> --detach                                        # hand the run to a coordinator in its own session
review-loop wait <run-id>                                                  # follow a run another process drives
review-loop start <pr url> --inspect-only                                  # review and assess into files; publish nothing
review-loop status | show <run-id> | pause <run-id> | resume <run-id> [--detach] | stop <run-id>
review-loop inbox list | show <id> | dismiss <id> --reason … | schedule <id> --reference … | resolve <id> --reference …
review-loop align <run-id> --file decision.md                              # optional override, never required
review-loop backup | restore <archive>
```

Inspect-only is a property of the run, fixed at `start`: a dry run can never be resumed into a fix or a
push. To run the same PR for real, `stop` it and `start` again (the worktree is reused); until then a
`start` in the other mode is refused with `mode_conflict`. The three
`publication` switches in the config (`post_reviews`, `post_author_responses`, `push_verified_fixes`)
are honoured at the point of each effect.

`start` and `resume` drive the run in the calling process and print one line per phase transition.
With `--detach` they hand the run to `review-loop drive <run-id>` in its own session, print its pid
and the path of `runs/<run-id>/coordinator.log`, and return. A run started that way outlives the
shell and the chat session that started it; `wait <run-id>` follows it, printing the same lines and
ending with the same exit code a foreground drive would. `status` and `show` mark a run in a working
state whose lock nobody holds as `(no coordinator)`, and `resume` continues such a run from the phase
it was in. A coordinator that crashes pauses its run as `coordinator_failed` with the traceback on
`show`; `resume` retries the phase.

Before the first phase, and again on every `resume`, each agent answers one structured probe with the
model and effort a phase would give it, through the same adapter and environment. A CLI that cannot
serve its model pauses the run as `agent_unavailable`, with the CLI's own error on `show`, before
anything is read or posted; `--no-preflight` skips the probe. `doctor` runs the same probes for every
distinct agent, model and effort the config names, and `--no-probe` skips them.

A run pauses, never guesses, on: a usage limit, expired auth, an agent that cannot serve its model, a
remote head that moved, a dirty or foreign worktree, a PR or fix that changes a registered script or a
file the agent CLI runs at startup, checks that fail twice or cannot run (a timed-out check counts as
unavailable), a commit in the worktree the coordinator did not make, a push the remote does not
confirm, a GitHub error, an agent that returns nothing usable, an agent that changed the worktree
during a read-only phase (`read_only_violated`; the change is left in place for you to inspect or
discard), or a crash of the coordinator itself. `resume` continues from the paused phase; after a
moved head it goes back through preparation, unless git shows the branch still where the run left it,
in which case the pause was the API lagging a push and the run continues where it paused. `stop` and
`pause` from another terminal take effect before the running loop's next phase: the phase already
running finishes first, `status` says `(coordinator still finishing its phase)` until it does, and a
review that finishes under a pause is kept as a checkpoint and published on resume instead of being
posted. No post and no push starts once one has landed: every post reserves its place in the outbox
in one statement that fails once a person's control is on the run, the push reserves the
coordinator's own record the same way, and an effect already reserved is the only thing that
completes. They stand
even when the coordinator records a pause of its own afterwards, from a failed probe or a crash
included. Both change only the run's control
state, so progress the coordinator persisted in the meantime is kept, and neither touches a run that
has finished: a complete, failed or cancelled run keeps its outcome, and the command reports the state
it found.

Agents run with no tokens, no credential helpers (`GIT_CONFIG_GLOBAL` empty, `GIT_CONFIG_NOSYSTEM`,
`GH_CONFIG_DIR` empty), pushes disabled through `GIT_CONFIG_*` variables, and a `gh` shim on PATH.
The developer's own git configuration is never touched. Everything posted to GitHub and every
commit message passes through the attribution filter; the author's desktop session is forked, never
appended to, and `start --author-session` is refused when the author agent cannot fork one.

## Agents

Each registered repository picks its reviewer and author in `config.toml`. Leave a model or effort
unset to use that agent's own default.

```toml
[repositories.webapp.review]
reviewer = "codex"                       # claude, codex, agy or opencode
author = "claude"
# reviewer_model = "gpt-5.6-sol"
# reviewer_effort = "xhigh"
# author_model = "anthropic/claude-opus-5-5"   # opencode takes provider/model
# author_effort = "xhigh"
```

| Agent | Structured output | Read-only phases held by | Developer's session |
|---|---|---|---|
| `claude` | `--json-schema` | tool allow and deny lists | found on disk and forked |
| `codex` | `--output-schema` | `--sandbox read-only` | not resumed |
| `agy` | `--json-schema` | `--sandbox` restricts the terminal; file writes are not blocked | not resumed |
| `opencode` | schema in the prompt, validated after | a tool-owned agent with edits and most commands denied | only with `--author-session`, forked |

Whatever the agent, every read-only phase ends with a check that nothing it may only read has changed:
HEAD, every file git reports as changed or untracked, and every file in the pass directory the agent is
given to read, each compared by content, mode and symlink target. The run pauses with
`read_only_violated` if anything did. Ignored files (dependencies, build output) are not compared, so
the agents with weaker read-only modes (`agy`, `opencode`) also rely on the environment the tool gives
them: no tokens, no credential helpers, pushes disabled. MCP servers are off for `claude` and `codex`.

A checkout can also declare code for an agent CLI to run at startup, before its permissions apply.
`claude -p` never asks for workspace trust, so `claude` starts with `--setting-sources ""` and reads
no settings file. That covers the checkout's `.claude/settings.json` and `.claude/settings.local.json`,
and your own `~/.claude/settings.json` too: a user hook runs with the checkout as its project
directory, and any `env` block overrides the guards in the environment the tool builds. Only managed
settings and the tool's own `--settings` apply, and `--strict-mcp-config` keeps `.mcp.json` off. Log in
with `claude` or `claude setup-token`; an `apiKeyHelper` in your settings does not reach these runs.
The flag also leaves every `CLAUDE.md` and `.claude/rules/` file out of Claude's context, yours
included. Every packet names the repository's `instruction_files` for the agent to read, so list
`.claude/CLAUDE.md` there if a repository keeps its instructions in it. `codex` starts with
`--ignore-user-config`, which also drops your list of trusted projects. The worktree then counts as
untrusted, and Codex loads none of its `.codex/` config, hooks, rules or MCP servers. Without the flag,
a worktree inherits the trust you gave its main checkout.

`agy` and `opencode` load the MCP servers in your own settings, and also run what the checkout declares
at startup (`.agents/hooks.json`, `.agents/mcp_config.json`, `.agents/plugins/` and `.agents/agents/` for
`agy`; `opencode.json` and `.opencode/` for `opencode`) before their permissions apply. Neither CLI can
switch that off, so the coordinator refuses to start either agent once the PR or a fix has changed one
of those files, or anything has left one there untracked (`scripts_changed`). That pins what the CLI
starts to the base branch, as the registered scripts are pinned; what it starts can still run the PR's
code, as the project's tests do in verification. The environment above is the boundary for all of it,
which is why only PRs from `allowed_pr_authors` run.

The `agy` and `opencode` adapters follow those CLIs' published docs and are covered by offline tests,
but neither has run against the real CLI yet. Try one PR with `--inspect-only` before letting either
fix code. Adding another CLI means one adapter in `review_loop/adapters/agents/` and an entry in
`AGENT_PROFILES` and `AGENT_ADAPTERS`.

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
