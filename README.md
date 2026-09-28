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

`repo add` writes `~/.review-loop/config.toml`. It registers `.claude/run-tests.sh` as the required
check when the project has one, or pytest when the project configures it; a registration with no
required check fails `doctor`, because a fix can never be verified without one. Everything the tool produces lives under
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
push. To run the same PR for real, `stop` it and `start` again (the worktree is reused). The three
`publication` switches in the config (`post_reviews`, `post_author_responses`, `push_verified_fixes`)
are honoured at the point of each effect.

A run pauses, never guesses, on: a usage limit, expired auth, a remote head that moved, a dirty or
foreign worktree, a PR or fix that changes a registered script, checks that fail twice or cannot run
(a timed-out check counts as unavailable), a commit in the worktree the coordinator did not make, a
push the remote does not confirm, a GitHub error, an agent that returns nothing usable, or an agent
that changed the worktree during a read-only phase (`read_only_violated`; the change is left in place
for you to inspect or discard). `resume`
continues from the paused phase; after a moved head it goes back through preparation. `stop` and
`pause` from another terminal take effect before the running loop's next phase.

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
# reviewer_model = "gpt-6-astra"
# author_model = "anthropic/claude-fable-5-1"   # opencode takes provider/model
```

| Agent | Structured output | Read-only phases held by | Developer's session |
|---|---|---|---|
| `claude` | `--json-schema` | tool allow and deny lists | found on disk and forked |
| `codex` | `--output-schema` | `--sandbox read-only` | not resumed |
| `agy` | `--json-schema` | `--sandbox` restricts the terminal; file writes are not blocked | not resumed |
| `opencode` | schema in the prompt, validated after | a tool-owned agent with edits and most commands denied | only with `--author-session`, forked |

Whatever the agent, every read-only phase ends with a check that HEAD and the working tree did not
change, and the run pauses with `read_only_violated` if they did. The check sees only the worktree, so
the agents with weaker read-only modes (`agy`, `opencode`) also rely on the environment the tool gives
them: no tokens, no credential helpers, pushes disabled. MCP servers are off for `claude` and `codex`;
`agy` and `opencode` load the ones in your own settings.

The `agy` and `opencode` adapters follow those CLIs' published docs and are covered by offline tests,
but neither has run against the real CLI yet. Try one PR with `--inspect-only` before letting either
fix code. Adding another CLI means one adapter in `review_loop/adapters/agents/` and an entry in
`AGENT_PROFILES` and `AGENT_ADAPTERS`.

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
